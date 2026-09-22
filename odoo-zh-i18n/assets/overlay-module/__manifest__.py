# Template of the overlay module a project uses to carry its Chinese
# translations.  Copy the whole directory to <project>/addons/<name> and edit
# this file only: everything else reads its own directory name, so the module
# can be renamed freely (the project convention here is
# sn_odoo<version>_translations).  Set `version` to <version>.x.y.z, keep the
# `post_load` entry, and let i18n_apply.py build the i18n/<lang>.po file.
{
    'name': 'SNCIC 中文翻译覆盖',
    'summary': '用独立模块补充/覆盖官方模块的中文译文，不改动上游源码',
    'description': """
SNCIC 中文翻译覆盖 (Odoo 20)
============================

本模块集中存放 TBB 项目需要补充的中文译文，避免把译文写进 odoo/ 上游源码树
（同步上游时会被 git reset --hard 清掉）。

数据类文案（记录字段、视图、邮件模板、支付方式等）：译文放在本模块的
i18n/zh_CN.po 里，条目照常带 model: / model_terms: 出处，指向官方模块的
xmlid，因此可以为任意模块补充译文。Odoo 在安装/升级本模块、启用语言时导入
它们；此外 models/ir_module_module.py 在注册表加载完毕后再导入一次，因为
Odoo 默认的导入时机是「逐个模块加载」的循环中，此时其它模块的模型还不存在，
本模块指向它们的条目会被静默丢弃。

代码类文案（python _() 、JS _t() 、QWeb）：Odoo 只会从「声明该字符串的模块
自己的 po 文件」读取，所以本模块在加载时把自己的条目合并进运行时缓存
odoo.tools.translate.code_translations，见 code_translation_overlay.py。

译文的编辑入口是项目里的 translations/zh_CN/ 工作清单，用技能 odoo-zh-i18n 的
i18n_apply.py build 生成 i18n/zh_CN.po。同步上游之后重跑该命令并升级本模块即可。
""",
    'version': '20.0.1.0.0',
    'author': 'SNCIC',
    'license': 'LGPL-3',
    'category': 'Hidden',
    'depends': ['base'],
    'post_load': 'post_load',
    'installable': True,
    'application': False,
}
