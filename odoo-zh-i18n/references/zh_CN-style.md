# Translating Odoo terms into Simplified Chinese

The goal is a UI that reads like Odoo's official Chinese, not a literal
translation. `i18n_apply.py check` enforces the mechanical rules; the rest is
judgement.

## Hard rules (the checker fails the build on these)

- **Placeholders stay byte identical**: `%s`, `%d`, `%(name)s`,
  `%(amount).2f`, `{brace}` expressions, `{{ ... }}` in QWeb text. Reorder them
  if the Chinese sentence needs it, never translate or drop them.
- **Markup tags stay identical** in name, count and nesting: `<b>`, `<br/>`,
  `<span class="...">`, `<t t-out="..."/>`. Translate only text nodes and
  `title`/`placeholder`-like values. Attributes, `t-*` directives and the
  whitespace inside them must not change.
- **Do not translate** brand and product names (Odoo, PayU, Toss Payments,
  Iyzico, Paymob, Redsys), technical identifiers (`source_transaction_id`,
  `id`, `x`), format strings (`^FS`), or entities (`&nbsp;`). These legitimately
  produce an "identical to the source" warning. A term with no sensible Chinese
  wording (a brand, a paper size, a single-letter shortcut) belongs in the
  project's `_intentional.txt` instead of a worklist, with a reason.
- Keep the leading/trailing whitespace and the line structure of the source
  string; Odoo uses some strings as labels where a trailing space matters.
- Only translate what you understand from the `#:` occurrence. Look the source
  file up (`code:addons/<module>/...`) when the term could belong to more than
  one screen.

## Terminology

Reuse what Odoo already ships rather than inventing a wording -- this is what
`i18n_tm.py` is for (`lookup` prints the variants the tree already uses, and
`fill` applies the unambiguous ones):

```bash
$VENV/bin/python scripts/i18n_tm.py lookup "Account" "Active" "Method" --addons <addons>
```

Preferred terms in the modules translated so far:

| English | zh_CN | note |
| --- | --- | --- |
| Active | 有效 | the field label; 启用 for the action "enable" |
| Account | 账户 / 科目 | 账户 for a login or payment account, 科目 for accounting |
| Invoice | 发票 | |
| Margin | 利润 | sale_margin uses 利润, not 保证金/边距 |
| Provider (payment) | 提供商 | |
| Token (payment) | 令牌 | |
| Live / Test | 正式 / 测试 | `payment.provider.state` |
| Post-process | 后处理 | |
| Credit (IAP) | 点数 | IAP credits are 点数, not 信用 |
| Tour | 导览 | `web_tour` |
| Archive | 归档 | |
| Unarchive | 取消归档 | |

## Working on a batch

- Translate one module at a time and look at the whole worklist before starting:
  the same word often appears as a label, a button and a report column in the
  same module, and they should agree.
- Prefer a script over hand-editing when a batch is large: keep
  `{msgid: msgstr}` in a dict, assign it to the worklist entries by msgid, and
  assert that the multiset of markup tags and placeholders is unchanged before
  writing. A typo in a long HTML mail template then fails your own run instead
  of shipping.
- For long templates (mail bodies, report views) do not retype the string at
  all: replace the individual text runs inside the source string and let
  `check` prove that the tags survived.
- After building (`i18n_apply.py build --module-dir <overlay> --write`) and
  reloading the module, re-scan: the terms you filled must disappear from the
  worklist, and anything left is either untranslatable noise or something you
  missed. A translation that is in the overlay po file but still English in the
  UI is usually a code term whose entry lost its `#. odoo-python` /
  `#. odoo-javascript` comment, or a data term the importer skipped (see
  `odoo-i18n-mechanics.md`).
- Reuse is cheap, guessing is not: run `i18n_tm.py fill` before translating so
  you only spend effort on what the tree has never translated, and run `lookup`
  for any term you are unsure about.
