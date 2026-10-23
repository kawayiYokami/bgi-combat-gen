# 全角色场景通用 · BetterGI 战斗策略

仓库：https://github.com/kawayiYokami/bgi-combat-gen

BetterGI 的 JSON 战斗策略与生成工具。一份策略覆盖全部角色与常见配队，用于锄大地、日常、幽境危战、深境螺旋等场景。

遇到异常的场景或配队，请到 [Issues](https://github.com/kawayiYokami/bgi-combat-gen/issues) 反馈；有更好的写法，欢迎 [PR](https://github.com/kawayiYokami/bgi-combat-gen/pulls)。

BetterGI 本体：https://github.com/babalae/better-genshin-impact

## 直接使用

1. 下载 `全角色场景通用.json`
2. 放进 BetterGI 安装目录下的 `User/AutoFight/`
3. 在自动战斗（或自动秘境等入口）里选择该策略

需要 BetterGI 0.62 以上版本（JSON 战斗策略）。

## 自行生成

```bash
python scripts/build.py                                  # 生成到 build/
python scripts/build.py --deploy <BetterGI 的 AutoFight 目录>   # 生成并部署
python scripts/check.py                                  # 只做语法检查
```

生成器会先展开模板、按优先级排序、写出策略，再做一遍语法自检；自检报错时不会部署。

## 修改角色

| 内容 | 位置 |
| --- | --- |
| 角色动作 | `assets/characters/<定位>/<角色名>.json` |
| 动作模板 | `assets/templates/<模板名>.json` |
| 先手动作 | `assets/pre_actions.json` |
| 兜底动作 | `assets/post_actions.json` |

定位目录决定出场段位：`盾` → `奶` → `副C` → `辅助` → `主C`，盾永远最先。一个角色可以有多套动作，靠条件与顺序分流。

## 目录结构

```
.
├── SKILL.md                  编写与改写策略的完整规则
├── 全角色场景通用.json         成品策略
├── scripts/
│   ├── build.py              生成入口
│   └── check.py              语法自检
└── assets/
    ├── characters/           角色文档，按定位分目录
    ├── templates/            动作模板
    ├── pre_actions.json      先手动作
    ├── post_actions.json     兜底动作
    └── combat_avatar.json    官方角色表，只用于登场顺序排序
```

## 说明

- 动作口径以实战效果为准，不照搬官方内置连招。
- 当前覆盖 120 个角色。新增角色按同样的文档格式补一份即可参与排序。
- 策略按「条件 + 优先级」驱动：每轮从高优先级往下扫，执行第一个条件成立的完整动作，然后重新扫描。
