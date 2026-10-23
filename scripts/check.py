# -*- coding: utf-8 -*-
"""BGI 战斗策略语法自检。

用法:
    python check.py                  # 检查部署产物 + 生成器源（默认）
    python check.py <产物.json> ...   # 额外/限定检查指定产物

产物层（BGI 运行时真正会读的语法）:
    - 条件表达式：token 合法性、括号配对、函数名/参数个数
    - 不支持 >= <=
    - since/last-exec/count 的动作名必须已在策略里声明
    - e-ready/q-ready/in-party 等参数是否为已知角色名
    - 动作串：命令白名单、禁用 aim/r/瞄准、键名格式、参数格式
    - 动作串残留未展开的 @模板
    - 动作名唯一、不与内置函数重名、不含非法字符

源层（生成器）:
    - 模板不能嵌套（@ 出现在模板 action 里，BGI 不会二次展开）
    - 角色/先手/兜底引用的 @模板 必须存在
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))       # scripts/
ROOT = os.path.dirname(HERE)                            # skill 根目录
ASSETS = os.path.join(ROOT, "assets")
CHARS = os.path.join(ASSETS, "characters")
TEMPLATES = os.path.join(ASSETS, "templates")
PRE = os.path.join(ASSETS, "pre_actions.json")
POST = os.path.join(ASSETS, "post_actions.json")
DEFAULT_PRODUCT = os.path.join(ROOT, "build", "全角色优先级策略.json")

# 内置条件函数 -> (最少参数, 最多参数)
FUNCS = {
    "since": (0, 1), "last-exec": (2, 3), "count": (3, 3),
    "min": (1, 99), "max": (1, 99),
    "t": (0, 0), "battle-time": (0, 0),
    "q-ready": (1, 1), "e-ready": (1, 1), "e-cd": (1, 1),
    "low-hp": (0, 0), "in-party": (1, 1), "onfield": (1, 1), "last-check": (0, 0),
}
NAME_ARG = {"since", "last-exec", "count"}          # 参数是「动作名」
CHAR_ARG = {"q-ready", "e-ready", "e-cd", "in-party", "onfield"}  # 参数是「角色名」
VAR_OK = {"t", "battle-time", "low-hp", "last-check", "true", "false"}

CMDS = {
    "e", "skill", "q", "burst", "attack", "普攻", "charge", "重击", "wait",
    "check", "检测", "dash", "冲刺", "j", "jump", "跳跃",
    "w", "a", "s", "d", "walk", "moveby",
    "keydown", "keyup", "keypress", "mousedown", "mouseup", "click", "scroll", "round",
}
BANNED = {"aim", "r", "瞄准"}                        # BGI 未实现，会抛异常
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")      # BGI 接受 VK_E 或简写 E / w / SPACE
VK_STD_RE = re.compile(r"^VK_[A-Z0-9_]+$")            # 规范写法（铁律8）
NUM_RE = re.compile(r"^\d+(\.\d+)?$")
NAME_BAD_CHARS = set(" \t\r\n,()&|><=!+-*/")

errors = []
warns = []


def err(m):
    errors.append(m)


def warn(m):
    warns.append(m)


def is_cjk(c):
    return "\u4e00" <= c <= "\u9fff"


def is_ident_start(c):
    return c.isalpha() or c == "_" or is_cjk(c)


def is_ident_char(c):
    return c.isalnum() or c == "_" or is_cjk(c)


# ---------------- 条件表达式 ----------------

def tokenize(s):
    toks, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        two = s[i:i + 2]
        if two in ("&&", "||", ">=", "<=", "==", "!="):
            toks.append(("op", two))
            i += 2
            continue
        if c in "><=!+-*/(),":
            toks.append(("op", c))
            i += 1
            continue
        if c.isdigit() or (c == "." and i + 1 < n and s[i + 1].isdigit()):
            j = i
            while j < n and (s[j].isdigit() or s[j] == "."):
                j += 1
            toks.append(("num", s[i:j]))
            i = j
            continue
        if is_ident_start(c):
            j = i + 1
            while j < n and (is_ident_char(s[j]) or
                             (s[j] == "-" and j + 1 < n and is_ident_char(s[j + 1]))):
                j += 1
            toks.append(("ident", s[i:j]))
            i = j
            continue
        raise ValueError("非法字符 %r" % c)
    return toks


class Parser:
    def __init__(self, toks):
        self.toks = toks
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, val):
        if self.peek() != ("op", val):
            raise ValueError("期望 %r，实际 %r" % (val, self.peek()[1]))
        return self.next()

    def parse_or(self):
        node = self.parse_and()
        while self.peek() == ("op", "||"):
            self.next()
            node = ("bin", "||", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_cmp()
        while self.peek() == ("op", "&&"):
            self.next()
            node = ("bin", "&&", node, self.parse_cmp())
        return node

    def parse_cmp(self):
        node = self.parse_add()
        if self.peek()[0] == "op" and self.peek()[1] in (">", "<", "="):
            op = self.next()[1]
            node = ("bin", op, node, self.parse_add())
        return node

    def parse_add(self):
        node = self.parse_mul()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.next()[1]
            node = ("bin", op, node, self.parse_mul())
        return node

    def parse_mul(self):
        node = self.parse_unary()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.next()[1]
            node = ("bin", op, node, self.parse_unary())
        return node

    def parse_unary(self):
        if self.peek() == ("op", "!"):
            self.next()
            return ("un", "!", self.parse_unary())
        if self.peek() == ("op", "-"):
            self.next()
            return ("un", "-", self.parse_unary())
        return self.parse_primary()

    def parse_primary(self):
        kind, val = self.peek()
        if kind == "num":
            self.next()
            return ("num", val)
        if kind == "ident":
            self.next()
            if self.peek() == ("op", "("):
                self.next()
                args = []
                if self.peek() != ("op", ")"):
                    args.append(self.parse_or())
                    while self.peek() == ("op", ","):
                        self.next()
                        args.append(self.parse_or())
                self.expect(")")
                return ("func", val, args)
            return ("var", val)
        if kind == "op" and val == "(":
            self.next()
            node = self.parse_or()
            self.expect(")")
            return node
        raise ValueError("意外的 token %r" % (val,))


def parse_condition(expr):
    toks = tokenize(expr)
    p = Parser(toks)
    node = p.parse_or()
    if p.i != len(toks):
        rest = toks[p.i]
        if rest in (("op", ">="), ("op", "<=")):
            raise ValueError("不支持 %s，请写成 x > a && x < b" % rest[1])
        raise ValueError("多余的 token %r" % (rest[1],))
    return node


def walk(node, ctx, decl, charnames, where):
    kind = node[0]
    if kind == "num":
        return
    if kind == "var":
        name = node[1]
        if ctx in NAME_ARG:
            if name not in decl:
                err("%s：%s() 引用了未声明的动作名「%s」" % (where, ctx, name))
        elif ctx in CHAR_ARG:
            if charnames and name not in charnames:
                warn("%s：%s() 的角色「%s」不在角色表" % (where, ctx, name))
        elif name not in VAR_OK and name not in FUNCS:
            err("%s：可疑标识符「%s」" % (where, name))
        return
    if kind == "func":
        fn, args = node[1], node[2]
        if fn not in FUNCS:
            err("%s：未知函数 %s()" % (where, fn))
            lo, hi = 0, 99
        else:
            lo, hi = FUNCS[fn]
        if not (lo <= len(args) <= hi):
            err("%s：%s() 参数个数 %d，应为 %d~%d" % (where, fn, len(args), lo, hi))
        for a in args:
            walk(a, fn, decl, charnames, where)
        return
    if kind == "bin":
        walk(node[2], ctx, decl, charnames, where)
        walk(node[3], ctx, decl, charnames, where)
        return
    if kind == "un":
        walk(node[2], ctx, decl, charnames, where)
        return


# ---------------- 动作串 ----------------

def split_tokens(action):
    tokens, buf, depth = [], [], 0
    for ch in action:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            tokens.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        tokens.append("".join(buf).strip())
    return [t for t in tokens if t]


def check_command(tok, where, walk_cmds, bad_keys):
    if tok.startswith("@"):
        err("%s：动作串残留未展开的模板引用 %s" % (where, tok))
        return
    if "(" in tok:
        idx = tok.index("(")
        name = tok[:idx].strip()
        if not tok.endswith(")"):
            err("%s：命令括号不闭合 %s" % (where, tok))
            return
        inner = tok[idx + 1:-1]
        args = [a.strip() for a in inner.split(",")] if inner.strip() else []
    else:
        name, args = tok.strip(), []
    if name in BANNED:
        err("%s：禁用命令 %s（BGI 未实现，会抛异常）" % (where, name))
        return
    if name not in CMDS:
        err("%s：未知命令 %s" % (where, tok))
        return
    if name in ("keydown", "keyup", "keypress"):
        if not args or not KEY_RE.match(args[0]):
            err("%s：%s 键名非法：%s" % (where, name, tok))
        elif not VK_STD_RE.match(args[0]):
            bad_keys.add(args[0])
    elif name == "wait":
        if not args or not NUM_RE.match(args[0]):
            err("%s：wait 需要数字：%s" % (where, tok))
    elif name == "moveby":
        if len(args) != 2 or not all(NUM_RE.match(a) for a in args):
            err("%s：moveby 需要两个数字：%s" % (where, tok))
    elif name in ("attack", "charge", "scroll"):
        if args and not NUM_RE.match(args[0]):
            err("%s：%s 参数应为数字：%s" % (where, name, tok))
    elif name in ("w", "a", "s", "d"):
        walk_cmds.add(name)
        if args and not NUM_RE.match(args[0]):
            err("%s：%s 参数应为秒数：%s" % (where, name, tok))
    elif name == "walk":
        walk_cmds.add(name)
    elif name in ("e", "skill"):
        if args and args[0] not in ("hold", "fast", "wait"):
            err("%s：e 的参数只能是 hold/fast/wait：%s" % (where, tok))
    elif name in ("q", "burst", "dash", "冲刺", "j", "jump", "跳跃",
                  "check", "检测", "round", "mousedown", "mouseup", "click"):
        if args:
            warn("%s：%s 带了参数，确认是否正确：%s" % (where, name, tok))


def check_action(action, where):
    if not (action or "").strip():
        err("%s：动作串为空" % where)
        return
    walk_cmds, bad_keys = set(), set()
    for tok in split_tokens(action):
        check_command(tok, where, walk_cmds, bad_keys)
    if walk_cmds:
        warn("%s：用了走位命令 %s，铁律8 建议改用 keydown/keyup 投键"
             % (where, "/".join(sorted(walk_cmds))))
    if bad_keys:
        warn("%s：键名 %s 非 VK_ 前缀，建议统一为 VK_（BGI 两种都能识别）"
             % (where, "/".join(sorted(bad_keys))))


# ---------------- 产物检查 ----------------

def check_product(path, charnames):
    d = json.load(open(path, encoding="utf-8"))
    acts = d.get("Actions") or []
    if not acts:
        err("产物 %s 没有 Actions" % path)
        return
    declared = [a.get("Name") or "" for a in acts]
    decl = {x for x in declared if x}

    seen = {}
    for x in declared:
        if x:
            seen[x] = seen.get(x, 0) + 1
    for x, c in seen.items():
        if c > 1:
            err("动作名重复：%s（%d 次）" % (x, c))
    for x in decl:
        if x in FUNCS:
            err("动作名与内置函数重名：%s" % x)
        bad = sorted({ch for ch in x if ch in NAME_BAD_CHARS})
        if bad:
            err("动作名含非法字符 %s：%s" % ("".join(bad), x))

    for a in acts:
        name = a.get("Name") or ""
        char = a.get("Character") or ""
        where = "Index %s %s" % (a.get("Index"), name or char)
        if char and charnames and char not in charnames:
            warn("%s：Character「%s」不在角色表" % (where, char))
        cond = (a.get("Condition") or {}).get("Expression", "") or ""
        if not cond.strip():
            if char:
                warn("%s：条件为空" % where)
        else:
            try:
                node = parse_condition(cond)
            except ValueError as e:
                err("%s：条件解析失败：%s  [%s]" % (where, e, cond))
                node = None
            if node is not None:
                walk(node, None, decl, charnames, where)
        check_action(a.get("Action") or "", where)


# ---------------- 源检查 ----------------

def check_template_refs(action, tpl, where):
    for m in re.finditer(r"@([A-Za-z0-9_\u4e00-\u9fff]+)", action):
        if m.group(1) not in tpl:
            err("%s：引用了不存在的模板 @%s" % (where, m.group(1)))
    if "@" in re.sub(r"@[A-Za-z0-9_\u4e00-\u9fff]+", "", action):
        err("%s：动作串里有非法 @（@ 后须紧跟模板名）" % where)


def check_source():
    tpl = {}
    if os.path.isdir(TEMPLATES):
        for fn in sorted(os.listdir(TEMPLATES)):
            if not fn.endswith(".json"):
                continue
            d = json.load(open(os.path.join(TEMPLATES, fn), encoding="utf-8"))
            nm = d.get("name") or fn[:-5]
            act = d.get("action", "") or ""
            tpl[nm] = act
            if "@" in act:
                err("模板「%s」里含 @：模板不能嵌套（BGI 不会二次展开）" % nm)

    if os.path.isdir(CHARS):
        for role in sorted(os.listdir(CHARS)):
            rd = os.path.join(CHARS, role)
            if not os.path.isdir(rd):
                continue
            for fn in sorted(os.listdir(rd)):
                if not fn.endswith(".json"):
                    continue
                d = json.load(open(os.path.join(rd, fn), encoding="utf-8"))
                for op in d.get("operations") or []:
                    where = "角色文档 %s/%s %s" % (role, fn, op.get("name") or "")
                    check_template_refs(op.get("action", "") or "", tpl, where)
                    if not (op.get("condition") or "").strip():
                        warn("%s：条件为空" % where)

    for p, label in ((PRE, "先手"), (POST, "兜底")):
        if os.path.exists(p):
            for spec in json.load(open(p, encoding="utf-8")):
                where = "%s %s" % (label, spec.get("name") or spec.get("character"))
                check_template_refs(spec.get("action", "") or "", tpl, where)


def load_char_names():
    names = set()
    if os.path.isdir(CHARS):
        for role in os.listdir(CHARS):
            rd = os.path.join(CHARS, role)
            if os.path.isdir(rd):
                for fn in os.listdir(rd):
                    if fn.endswith(".json"):
                        names.add(fn[:-5])
    for p in (PRE, POST):
        if os.path.exists(p):
            for spec in json.load(open(p, encoding="utf-8")):
                names.add(spec.get("character", ""))
    names.discard("")
    return names


def run(products=None, do_source=True):
    """执行检查，返回 (errors, warns)。可被 build.py 作为部署门禁调用。"""
    del errors[:]
    del warns[:]
    if do_source:
        check_source()
    charnames = load_char_names()
    for p in (products or [DEFAULT_PRODUCT]):
        if not os.path.exists(p):
            warn("产物不存在，跳过：%s" % p)
            continue
        check_product(p, charnames)
    return list(errors), list(warns)


def main():
    products = sys.argv[1:] or [DEFAULT_PRODUCT]
    errs, wns = run(products)
    print()
    for m in wns:
        print("WARN " + m)
    for m in errs:
        print("ERR  " + m)
    print()
    print("汇总：ERR %d，WARN %d" % (len(errs), len(wns)))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
