"""把 generator/characters 下的角色文档，生成 BetterGI 的 JSON 战斗策略。

数据来源（全手写，不依赖 gcsim）：
    generator/characters/<定位>/<官方中文名>.json   每个角色一个文档，内含多条操作
    generator/templates/<模板名>.json               动作模板，角色文档用 @模板名 引用
    generator/pre_actions.json                      先手动作（Index 1~9）
    generator/post_actions.json                     兜底动作（排在所有角色之后，优先级最低）

产出：
    build/characters/<中文名>.json   每个角色一个文件
    build/全角色场景通用.json       合并后的总调度（拷到 User\\AutoFight 即可）
"""

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))       # scripts/
ROOT = os.path.dirname(HERE)                            # skill 根目录
sys.path.insert(0, HERE)
import check as syntax_check  # noqa: E402  同目录语法自检

ASSETS = os.path.join(ROOT, "assets")
CHARS = os.path.join(ASSETS, "characters")
TEMPLATES = os.path.join(ASSETS, "templates")
PRE = os.path.join(ASSETS, "pre_actions.json")
POST = os.path.join(ASSETS, "post_actions.json")
OUT_DIR = os.path.join(ROOT, "build")
CHAR_DIR = os.path.join(OUT_DIR, "characters")
MERGED = os.path.join(OUT_DIR, "全角色场景通用.json")

# BGI 自己的角色表：按 id 递增 = 登场顺序（新角色 id 更大）。作为「登场次序」依据，
# 取代各角色文档里手填的 version（新角色常常没填，会被当成 0 而错误沉底）。
AVATAR_TABLE = os.path.join(ASSETS, "combat_avatar.json")


def load_release_ids():
    ids = {}
    try:
        with open(AVATAR_TABLE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ids
    items = data if isinstance(data, list) else (data.get("avatars") or list(data.values()))
    for it in items:
        if not isinstance(it, dict):
            continue
        name, rid = it.get("name"), it.get("id")
        if not name or rid is None:
            continue
        try:
            ids[name] = int(rid)
        except (TypeError, ValueError):
            continue
    return ids


RELEASE_IDS = load_release_ids()

# 定位优先级：盾 < 奶 < 副C < 辅助 < 主C
ROLE_RANK = {"盾": 0, "奶": 1, "副C": 2, "辅助": 3, "主C": 4, "其他": 2}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_templates():
    templates = {}
    if os.path.isdir(TEMPLATES):
        for fn in sorted(os.listdir(TEMPLATES)):
            if not fn.endswith(".json"):
                continue
            doc = load_json(os.path.join(TEMPLATES, fn))
            templates[doc["name"]] = doc["action"]
    return templates


TEMPLATE_MAP = load_templates()


def expand(action):
    """把动作串里的 @模板名 展开成实际动作串。引用不存在的模板直接报错。"""
    tokens = []
    for token in action.split(","):
        token = token.strip()
        if not token:
            continue
        if token.startswith("@"):
            name = token[1:]
            if name not in TEMPLATE_MAP:
                raise KeyError(f"未知动作模板：@{name}")
            tokens.extend(x.strip() for x in TEMPLATE_MAP[name].split(",") if x.strip())
        else:
            tokens.append(token)
    return ", ".join(tokens)


def collect_characters():
    fragments = []
    if not os.path.isdir(CHARS):
        return fragments
    for role in sorted(os.listdir(CHARS)):
        role_dir = os.path.join(CHARS, role)
        if not os.path.isdir(role_dir):
            continue
        for fn in sorted(os.listdir(role_dir)):
            if not fn.endswith(".json"):
                continue
            doc = load_json(os.path.join(role_dir, fn))
            zh = fn[:-5]
            entries = [
                {
                    "name": op.get("name") or zh,
                    "condition": op.get("condition") or f"e-ready({zh})",
                    "action": expand(op["action"]),
                }
                for op in doc["operations"]
            ]
            fragments.append({
                "character": zh,
                "role": role,
                "god": doc.get("god", False),
                "tail": doc.get("tail", False),
                "duration": doc.get("duration", 0),
                "version": doc.get("version", "0"),
                "release_id": RELEASE_IDS.get(zh, 0),
                "entries": entries,
                "confidence": doc.get("confidence"),
                "note": doc.get("note"),
                "sources": doc.get("sources", []),
            })
    return fragments


def seq_key(frag):
    """出场次序：定位（盾<奶<副C<辅助<主C）→ 神明 → 贴底 → 持续时间长 → 登场次序（新者靠前，按 BGI 角色表 id）。

    文档里写 "tail": true 的角色，先排空到本定位段的最后（贴底）。
    """
    role = frag["role"]
    duration = frag["duration"] if role != "主C" else 0
    return (
        ROLE_RANK.get(role, 2),
        0 if frag["god"] else 1,
        1 if frag["tail"] else 0,
        -duration,
        -frag["release_id"],
        frag["character"],
    )


def main():
    fragments = collect_characters()
    ranked = sorted(fragments, key=seq_key)

    pre_specs = load_json(PRE) if os.path.exists(PRE) else []
    post_specs = load_json(POST) if os.path.exists(POST) else []

    # 出场次序分四段：盾档 → 先手动作（超前批，火神那批）→ 其余定位 → 兜底动作。
    # 盾永远最先；先手动作排在盾之后、所有非盾角色之前。
    shields = [f for f in ranked if f["role"] == "盾"]
    others = [f for f in ranked if f["role"] != "盾"]

    os.makedirs(CHAR_DIR, exist_ok=True)
    actions = []
    cursor = 0

    def append_frag(frag):
        nonlocal cursor
        cursor += 10
        frag["priority"] = cursor
        for k, ent in enumerate(frag["entries"]):
            actions.append({
                "Name": ent["name"],
                "Character": frag["character"],
                "Index": cursor + k,
                "Condition": {"Expression": ent["condition"]},
                "Action": ent["action"],
            })

    for frag in shields:
        append_frag(frag)

    for spec in pre_specs:
        cursor += 10
        actions.append({
            "Name": spec["name"],
            "Character": spec["character"],
            "Index": cursor,
            "Condition": {"Expression": spec["condition"]},
            "Action": expand(spec["action"]),
        })

    for frag in others:
        append_frag(frag)

    for spec in post_specs:
        cursor += 10
        actions.append({
            "Name": spec["name"],
            "Character": spec["character"],
            "Index": cursor,
            "Condition": {"Expression": spec["condition"]},
            "Action": expand(spec["action"]),
        })

    for frag in ranked:
        payload = {
            "Info": {
                "Name": frag["character"],
                "Author": "kawayiYokami",
                "Config": {
                    "来源": f"角色文档 generator/characters/{frag['role']}/{frag['character']}.json",
                    "核对状态": ("已按攻略核对（置信度 " + frag["confidence"] + "）")
                               if frag["confidence"] else "未标注置信度",
                    "定位": frag["role"],
                    "依据": frag["sources"],
                    "优先级": frag["priority"],
                    "备注": [frag["note"]] if frag["note"] else [],
                },
            },
            "Actions": [
                {
                    "Name": ent["name"],
                    "Character": frag["character"],
                    "Index": frag["priority"] + k,
                    "Condition": {"Expression": ent["condition"]},
                    "Action": ent["action"],
                }
                for k, ent in enumerate(frag["entries"])
            ],
        }
        with open(os.path.join(CHAR_DIR, f"{frag['character']}.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)

    merged = {
        "Info": {
            "Name": "全角色场景通用",
            "Author": "kawayiYokami",
            "Config": {
                "介绍": "任何场景、任何配队通用：锄大地、日常、幽境危战、深境螺旋等均可直接使用。遇到异常场景或配队，请到项目仓库提 issue 或 PR。",
                "模型": "每轮按 Index 从高优先级往下扫，第一个条件成立的角色出场，打完自己一整套后回到决策环节。",
                "排序": "先盾档（永远最先）→ 先手动作（超前批，火神那批）→ 其余定位（奶<副C<辅助<主C）→ 兜底动作；同种内 神明优先 > 持续时间长 > 登场版本靠后。",
                "生成": "generator/build.py，来源 generator/characters/ + generator/templates/",
                "角色数": len({a["Character"] for a in actions if a["Character"]}),
                "条目数": len(actions),
                "相关角色": sorted({a["Character"] for a in actions if a["Character"]}),
            },
        },
        "Actions": actions,
    }
    with open(MERGED, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)

    print(f"角色片段 {len(ranked)} 个 -> {CHAR_DIR}")
    print(f"总调度 -> {MERGED}")

    # 语法自检：通过才部署到 User/AutoFight/，出错就停在这里。
    errs, wns = syntax_check.run([MERGED])
    for m in wns:
        print("  WARN " + m)
    if errs:
        for m in errs:
            print("  ERR  " + m)
        print(f"语法自检未通过（ERR {len(errs)}），未部署到 User/AutoFight/。")
        sys.exit(1)
    print(f"自检通过（WARN {len(wns)}）")
    if "--deploy" in sys.argv:
        i = sys.argv.index("--deploy")
        if i + 1 < len(sys.argv):
            dest_dir = os.path.abspath(sys.argv[i + 1])
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, "全角色场景通用.json")
            shutil.copyfile(MERGED, dest)
            print(f"已部署 -> {dest}")
    else:
        print("未部署（需要时加 --deploy <目标目录>）")


if __name__ == "__main__":
    main()
