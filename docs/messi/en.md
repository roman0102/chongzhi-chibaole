# Messi Desktop Pet — English Guide

[中文](zh-CN.md) · [English](en.md) · [Español](es.md) · [日本語](ja.md) · [한국어](ko.md)

An Argentina No. 10 plush desktop companion. Both editions include the latest poses: large hands beside the ears, a serious unblinking expression, and a larger envelope held up with both eyes open. The artwork adapts a user-provided plush reference; it does not imply an official partnership.

## Choose an edition

| Edition | Download | Requirements |
| --- | --- | --- |
| Codex-connected | [Download the Skill ZIP](https://github.com/roman0102/chongzhi-chibaole/raw/refs/heads/main/downloads/messi-codex-skill-windows-v1.0.0.zip) | Windows; Codex running and signed in; Python 3.10+, Tk and Pillow |
| Standalone loop | [Download the offline EXE ZIP](https://github.com/roman0102/chongzhi-chibaole/raw/refs/heads/main/downloads/messi-offline-windows-v1.0.0.zip) | 64-bit Windows; no Codex, Python installation, account or internet needed |

Tested on the current Windows 11 machine. No macOS, Linux or mobile package is provided. These guides are translated; the app menus are still in Chinese.

## Standalone: unzip and play

1. Download and fully extract the standalone ZIP, then double-click `Messi-Offline.exe`.
2. Every six seconds it advances through: idle → dribble right → hands-to-ears celebration → kiss the trophy → show the envelope → talking gesture → dribble left → puzzled expression, then repeats.
3. Drag with the left mouse button to reposition. Click or right-click for the menu. The toy moves across the desktop while dribbling and stops moving while hovered.
4. “选择并保持动作” means **choose and hold an action**. Enable “自动切换动作” to resume the loop. “下一个动作” means **next action**.
5. Close with Esc, the × shown on hover, or “退出玩偶” (**quit**). Reopening the EXE does not create a second standalone instance.

**The standalone envelope, trophy and speech bubbles are scripted animations. They do not represent real mail, usage limits or tasks.** This edition connects to neither Codex nor Gmail and adds no scheduled task or startup entry. Its runtime and artwork are inside the EXE; source files are optional.

## Codex-connected edition

1. Extract the connected ZIP. Give Codex the included `quota-pet` folder and ask:

   > Follow this folder's SKILL.md to run assets/messi/skin.json, connect my real usage limits and local work status, and create a desktop shortcut with the plush icon. Leave Gmail disabled.

2. Alternatively, run these commands inside `quota-pet` with your installed Python:

```powershell
python -m pip install Pillow
python scripts/quota_pet.py --skin assets/messi/skin.json --background
python scripts/create_shortcut.py --skin assets/messi/skin.json
```

3. Hover for the status card. Click or right-click to preview poses. “刷新真实额度” (**refresh real usage limits**) exits quota preview mode. The Windows shortcut uses the plush icon.

| Live condition | Animation |
| --- | --- |
| Stationary, 100% remaining | Kisses the gold trophy |
| Stationary, 80% to less than 100% | Snaps upright, large hands at ears, serious and unblinking |
| 0% remaining | Puzzled/pouting head turn; stops roaming; bubble: ¿Qué mirás, bobo? |
| Working or roaming while idle | Dribbles left or right |
| Waiting for your reply or approval | Talking gesture with ¿Qué mirás, bobo? |
| New mail from a separately authorized Gmail check | Holds up the large envelope with a serious, open-eyed face |

In live mode, waiting/error states take priority over mail; mail takes priority over quota poses. Work and roaming use dribbling. Quota preview deliberately shows the selected pose without changing the real work record. Unknown quota is never treated as zero. Usage limits refresh every five minutes by default; the window with the lowest remaining percentage is displayed, with additional credits handled separately.

Gmail is **off by default**. Connecting a Gmail plugin does not authorize ongoing monitoring. Explicitly enabled checks run periodically through Codex, use Codex allowance, and are not instant push notifications. Only necessary mail metadata is used; acknowledging a local notice does not mark mail as read. Closing the pet does not stop a separately scheduled mail check: pause that task too. Setup details are in the package's `references/gmail.md`.

Normal rendering, quota reads and work-status observation do not call a model or need an extra API key. Work status uses a local compatibility interface that may need updates after Codex changes. Its initial snapshot can contain conversation content; the program reduces it to status fields in memory and discards message bodies without saving the raw snapshot.

## Preview and help

<img src="previews/trophy.gif" width="126" alt="Kissing the gold trophy"><img src="previews/ears.gif" width="126" alt="Serious celebration with large hands at ears"><img src="previews/mail.gif" width="126" alt="Holding up a large envelope without blinking">

These previews are exported from actual animation frames, not screenshots of live account data. “¿Qué mirás, bobo?” roughly means “What are you looking at, fool?” The line is a text bubble, with no spoken audio.

- To inspect a pose, use “动作试玩” (**action preview**) in the connected edition, or “选择并保持动作” in the standalone edition.
- If quota cannot be read, check your Codex sign-in; failures should show an unknown state.
- To remove an edition, quit it and delete its files and shortcut. Pause any separately enabled Gmail task first.
- These are separate desktop windows, launched outside Codex's built-in pet menu. Both editions can coexist.

No license granting unrestricted commercial use of the code or character artwork has been declared.
