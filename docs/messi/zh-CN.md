# 梅西桌面宝宝：中文使用指南

[中文](zh-CN.md) · [English](en.md) · [Español](es.md) · [日本語](ja.md) · [한국어](ko.md)

阿根廷 10 号毛绒公仔桌宠。两个版本都使用同一套最新动作：捂耳时大手、严肃、不眨眼；举邮件时严肃、全程睁眼、展示大信封。这些是基于用户提供公仔图片制作的动画，不表示官方合作。

## 选择版本

| 版本 | 下载 | 运行条件 |
| --- | --- | --- |
| Codex 联动版 | [下载 Skill ZIP](https://github.com/roman0102/chongzhi-chibaole/raw/refs/heads/main/downloads/messi-codex-skill-windows-v1.0.0.zip) | Windows；已登录并运行 Codex；Python 3.10+、Tk、Pillow |
| 独立循环版 | [下载离线 EXE ZIP](https://github.com/roman0102/chongzhi-chibaole/raw/refs/heads/main/downloads/messi-offline-windows-v1.0.0.zip) | Windows 64 位；无需 Codex、Python、账号或联网 |

已在当前 Windows 11 电脑验证，未提供 macOS、Linux、手机安装包。这里翻译的是说明与使用指南；程序菜单目前仍为中文。

## 独立循环版：解压即玩

1. 下载独立版 ZIP，完整解压，双击 `Messi-Offline.exe`。
2. 默认每 6 秒切换：站一会儿 → 向右带球 → 捂耳庆祝 → 亲吻金杯 → 举起大信封 → 说两句 → 向左带球 → 疑惑表情，随后循环。
3. 左键拖动换位置；点击或右键打开菜单。带球时会在桌面移动，鼠标悬停时暂停移动。
4. “选择并保持动作”可以固定动作；勾选“自动切换动作”恢复循环。“下一个动作”立即跳到下一项。
5. Esc、悬停出现的 × 或“退出玩偶”关闭。重复双击只保留一个独立版实例。

**独立版的信封、金杯和西语气泡都是固定动画，不读取真实邮件、额度或工作状态。** 不连接 Codex/Gmail，不添加定时任务或开机启动。EXE 自带运行时和素材，源代码不是运行必需品。

## Codex 联动版

1. 下载并解压联动版 ZIP。将其中 `quota-pet` 文件夹交给 Codex，发送：

   > 请按这个文件夹的 SKILL.md 启动 assets/messi/skin.json，接入真实额度与本机工作状态，创建公仔图标的桌面快捷方式。暂不启用 Gmail。

2. 也可在 `quota-pet` 文件夹中用实际安装的 Python 运行：

```powershell
python -m pip install Pillow
python scripts/quota_pet.py --skin assets/messi/skin.json --background
python scripts/create_shortcut.py --skin assets/messi/skin.json
```

3. 悬停看状态卡；点击/右键可试玩额度和动作。“刷新真实额度”退出模拟模式。Windows 快捷方式使用公仔头像。

| 实时条件 | 动作 |
| --- | --- |
| 静止且剩余 100% | 亲吻金杯 |
| 静止且剩余 80% 至不足 100% | 突然站直，大手捂耳，严肃不眨眼 |
| 剩余 0% | 瞪眼、撇嘴、转头，停止漫游，并显示 ¿Qué mirás, bobo? |
| 工作或空闲漫游 | 左右带球 |
| 等待回复或批准 | 挥手说话，同时显示 ¿Qué mirás, bobo? |
| 单独授权的 Gmail 检查发现新邮件 | 举起大信封，严肃睁眼 |

实时模式下，等待和错误状态优先于邮件，邮件优先于额度动作；工作/漫游时会带球。额度试玩会优先展示选中的造型，真实工作记录不变。未知额度不当作 0%。额度默认每 5 分钟读取一次；多个窗口取剩余比例最低的一项，额外点数单独处理。

Gmail **默认关闭**。连接插件不代表同意持续监控；仅在明确授权后通过 Codex 定时检查，会使用 Codex 额度，不是即时推送。只取必要邮件元数据，确认提醒不修改邮件已读状态。关闭玩偶不会停止另外创建的定时任务，停用时还要暂停该任务。详见包内 `references/gmail.md`。

普通绘制、额度与工作状态连接不调用模型，不需要额外 API key。工作连接依赖本机兼容接口，Codex 更新后可能需要适配；初始工作快照可能包含对话内容，脚本在内存中缩减为状态字段后丢弃正文，不保存原始快照。

## 动作预览与常见问题

<img src="previews/trophy.gif" width="126" alt="亲吻金杯动作"><img src="previews/ears.gif" width="126" alt="严肃大手捂耳庆祝"><img src="previews/mail.gif" width="126" alt="严肃展示大信封">

预览从实际动画帧导出，不是实时账号状态截图。西语气泡意为“你看什么，笨蛋？”，是玩偶台词，没有语音播放。

- 看不到某个动作：联动版可用菜单“动作试玩”；独立版可用“选择并保持动作”。
- 无法连接额度：先确认 Codex 已登录；读取失败应显示未知。
- 退出与移除：关闭玩偶，删除该版本文件和快捷方式；若启用过 Gmail，先停掉其定时任务。
- 这是独立桌面窗口，不从 Codex 内置宠物菜单启动。两版可以并存。

项目尚未声明代码或角色素材可自由商用的许可。
