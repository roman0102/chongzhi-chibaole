# 重置吃饱了 🐾

给 Codex 养一只会被你饿瘦的桌宠。额度满时胖成球，用着用着就瘦了，额度恢复后再长回来。

清野凛示例还能跟随本机 Codex 桌面聊天的工作状态走路、招手、开心和哭泣。平时只显示角色和一个小状态点，鼠标移上去才显示详情。

这是一个个人制作的 **Windows 实验版 Skill**，包含制作指引、运行脚本和角色素材。桌宠以独立悬浮窗运行。

<img src="skills/quota-pet/assets/seinorin/seinorin-quota.png" width="720" alt="清野凛的不同额度体型示意">

*上图是体型示意图。运行时另有走路和表情动画。*

## 安装和开始玩

需要 Windows、已登录且正在运行的 Codex 桌面端，以及含 Tk 的 Python 3.10+。清野凛图片皮肤另需 Pillow。可以让 Codex 检查并协助准备环境。

把本仓库链接交给 Codex，并告诉它：

```text
请从这个仓库的 skills/quota-pet 目录安装「重置吃饱了」Skill：
https://github.com/roman0102/chongzhi-chibaole
安装完成后告诉我如何启动清野凛示例，并检查运行环境。
```

安装后，在下一轮对话发送：

```text
用 $quota-pet 启动清野凛，接入真实额度和本机 Codex 工作状态。
```

也可以下载仓库 ZIP，将 `skills/quota-pet` 整个目录放入自己的 Codex skills 目录，默认位置是 `~/.codex/skills/quota-pet`；如果设置了 `CODEX_HOME`，使用其中的 `skills` 目录。已经装过同名 Skill 时，让 Codex 检查已有版本后再更新。

只想先看效果，可以说“用模拟额度试玩清野凛”。模拟额度不要求账号登录；工作状态连接会独立尝试读取本机桌面状态。

## 可以怎么玩

- 额度决定胖瘦。默认每 5 分钟查询一次，也能在菜单里手动刷新。
- 清野凛在工作时走动，等你回复或批准时招手，完成时开心，出错时哭一下。
- 空闲时自由溜达，拖动可以换位置，悬停会暂停移动并展开信息卡。
- 点击角色打开菜单，试玩不同体型和表情，也能暂停溜达或退出。信息卡上的 × 和 Esc 同样可以退出。
- 可以让 Codex 按 Skill 指引，换成你自己的角色或制作新的图片皮肤。

没有图片皮肤时，默认小猪只依赖 Python 标准库。它有胖瘦、状态显示和漫游，但没有清野凛的整套招手、开心、哭泣动画。

## 边界和数据说明

**当前完整体验只在 Windows 上验证。** 其他系统的工作状态连接尚未实现。这个 Skill 不会改写 Codex 安装包或原有宠物设置，也不会自动充值、重置额度、添加开机启动或替你操作任务。

额度来自已登录 Codex 的 `account/rateLimits/read`。多个额度窗口中选择剩余比例最低的一项显示，额外点数单独显示。它不是逐 token 的实时计费表，读取失败会显示未知。运行时不调用模型生成内容，也不需要另外填写 API key；额度查询仍需要 Codex 账号连接。

工作状态使用本机 Windows 桌面的兼容接口，已核对桌面版本 `26.1021.3705.0`。该接口不是公开稳定 API，Codex 更新后可能需要适配。默认从最近 50 个未归档聊天 ID 开始发现并持续跟随可用的本机聊天，不覆盖所有远程或云端任务。

连接工作状态时，桌面会发送一次包含会话内容的初始快照。脚本在内存中解析后仅保留状态字段，丢弃正文和工具内容，不将原始快照保存、记录到日志或发送出去。长聊天初次连接可能出现较高瞬时内存占用，不能把它理解成始终低内存的小挂件。具体状态范围和测试数据见 [工作状态说明](skills/quota-pet/references/work-status.md)。

## 自己运行或改角色

在 `skills/quota-pet` 目录下运行，使用实际安装的 Python：

```powershell
python -m pip install Pillow
python scripts/codex_quota.py --json
python scripts/quota_pet.py --name 清野凛 --skin assets/seinorin/skin.json --background
```

默认小猪可以直接运行 `python scripts/quota_pet.py --background`，无需安装 Pillow。图片皮肤格式见 [皮肤说明](skills/quota-pet/references/skins.md)，完整制作流程见 [SKILL.md](skills/quota-pet/SKILL.md)。

卸载时先通过菜单退出桌宠，再移除已安装的 `quota-pet` Skill 目录即可。

## 项目与素材

这是个人实验项目。仓库包含清野凛的原始动作素材和为不同体型生成的图片，生成提示词随素材保留。

当前代码和角色素材尚未附许可文件，复用与再分发的许可范围待作者确认。暂不以 MIT 或自由商用项目宣传。
