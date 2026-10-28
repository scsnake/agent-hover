# agent-hover

[English](README.md)

在 **iTerm2** 裡只顯示少數幾個 AI 程式代理分頁。其他代理分頁會隱藏並繼續執行，需要你時再自己回來。一個小型浮動面板讓你一眼看到所有代理。支援 macOS 上的 Claude Code、Codex 與 Antigravity CLI（`agy`）。

![上：面板列出所有代理分頁，沒在工作卻很忙的代理有紅色火焰，舊分頁收合成一行，左邊是該代理的 CPU 與記憶體。下：搜尋與 Closed 區段](docs/panel.png)

- **讓分頁列保持簡短。** 工具最多只顯示 N 個代理分頁。其他分頁會變成 iTerm2 的 **buried sessions**（隱藏工作階段），程式仍在背景執行。
- **需要時分頁會自己回來。** 代理等你回覆或完成工作時，它的分頁會回到原本的位置。但你打字時不會。
- **一眼看到所有代理。** 螢幕右上角的小膠囊顯示最近活動的代理。滑鼠移上去，就會列出所有代理分頁：工作中、等你回覆、已完成、閒置。點一下就會把該分頁和 iTerm2 帶到最前面。

> **狀態：概念驗證（POC）。** 作者每天在一台 Mac 上使用。可能還有不完善的地方。測試環境：macOS 15.8、iTerm2 3.7.3、Claude Code 2.1、Codex CLI。只支援 macOS 與 iTerm2。

## 功能

| 功能 | 說明 |
|---|---|
| 代理狀態 | Claude Code、Codex 與 Antigravity CLI（`agy`）的 hook 會寫入每個分頁的狀態：**工作中**、**等待中**（權限、提問、計畫）、**已完成**、**閒置** |
| N 個使用中分頁 | 背景程式會隱藏閒置或長時間工作的分頁；代理等待或完成時，再把分頁叫回來。它不會改分頁標題 |
| 浮動面板 | 滑鼠移到膠囊上，會依最近活動順序列出所有代理分頁。點一列就會叫出該分頁 |
| 問題預覽 | 等待中的代理有橘色 `?`。滑鼠移到該列，會顯示問題內容：要核准的指令、選項或計畫 |
| 偷看（Peek） | 滑鼠停在其他列上，會顯示該分頁畫面的最後幾行，不必切換過去 |
| CPU 與記憶體 | 滑鼠停在列右端的代理標記上，小框會改為顯示代理、模型，以及代理和它執行的指令所用的 CPU 與記憶體。代理沒在工作卻用掉很多 CPU 時（卡住，或有指令還在跑），會出現紅色火焰標記 |
| 「新」圓點 | 藍色圓點標示已完成、但你還沒看過的分頁 |
| 釘選 | 在某列按右鍵 → **Pin**。釘選的分頁不會被隱藏，並排在最前面 |
| 搜尋 | 點放大鏡。可依分頁名稱、代理資料夾篩選，也可選擇搜尋對話內容。最近 7 天關閉的代理列在 **Closed** 下方；點一下會在該資料夾開新分頁，並輸入恢復對話的指令（按 Enter 才執行） |
| 邊緣細條 | 把膠囊拖到螢幕右邊緣，再往外推一點並停一下（出現藍框），然後放開，它會變成一條細條，顏色代表最緊急的狀態。滑鼠移上去就會展開面板；把它拖離邊緣即可還原 |
| 模型 | 滑鼠停在某列，會顯示代理與所用的模型（也包含自訂供應商，例如 Codex 接本機模型） |
| 安全輸入 | 自動叫回分頁前，會等你停止打字 3 秒，避免按鍵送到錯的代理 |
| 遠端主機 | 其他機器（SSH）上 tmux 窗格裡的代理，也會列在同一個面板，並帶伺服器標記。唯讀：主機上不安裝任何東西。見[遠端主機](#遠端主機) |
| 設定 | `~/.agent-hover/config.json`：字型大小、面板寬度、N、閒置時間 |

## 需求

- macOS（測試於 15.8）與 **iTerm2 3.7 以上**，並開啟 Python API（**Settings → General → Magic → Enable Python API**）。
- [uv](https://docs.astral.sh/uv/)（Python 套件管理工具）。
- Xcode Command Line Tools（`xcode-select --install`）。面板是一個 Swift 檔，第一次執行 `./run.sh panel` 時會在你的 Mac 上編譯。所以不需要下載未簽署的 App。
- Claude Code 和／或 Codex CLI。

`./run.sh doctor` 會檢查以上所有項目，並為每個問題提供修正方法。

## 安裝

1. 取得程式碼：
   ```sh
   git clone https://github.com/scsnake/agent-hover.git
   cd agent-hover
   ```
2. 先看安裝會改什麼。這一步不會寫入任何檔案：
   ```sh
   ./install.sh
   ```
3. 加入 hook。它會先備份 `~/.claude/settings.json`、`~/.codex/config.toml`，以及（有安裝 agy 時）`~/.gemini/config/hooks.json`：
   ```sh
   ./install.sh --apply
   ```
4. 只有 Codex 需要：啟動 `codex`，輸入 `/hooks`，信任新的 hook。Codex 會略過未信任的 hook。
5. 先用 **dry run**（試跑）啟動背景程式。它只記錄會做什麼：
   ```sh
   ./run.sh start
   ./run.sh log
   ```
6. 確認決定合理後，正式啟動背景程式與面板：
   ```sh
   ./run.sh stop
   ./run.sh            # 等同 ./run.sh up
   ```
7. 選用：登入時、以及每次 iTerm2 執行時自動啟動：
   ```sh
   ./run.sh autostart on
   ```
   macOS 可能會詢問一次是否允許它控制 iTerm2，請允許。`./run.sh autostart off` 可移除。

請在一般的 shell 分頁啟動背景程式，不要在代理的工作階段裡啟動。

## 使用

| 動作 | 結果 |
|---|---|
| 滑鼠移到膠囊上 | 展開清單 |
| 滑鼠移開 | 0.4 秒後收起清單 |
| 點某列，或點膠囊 | 該分頁回來（若被隱藏）並被選取，iTerm2 移到最前面 |
| 滑鼠停在等待中的列 | 清單旁的小框顯示代理的問題 |
| 滑鼠停在其他列 | 0.35 秒後，小框顯示該分頁畫面的最後幾行（偷看） |
| 在某列按右鍵 | **Pin** / **Unpin**（釘選／取消釘選） |
| 點放大鏡 | 搜尋模式：輸入文字即篩選。勾選 **chat** 可搜尋對話內容。**Enter** 叫出第一個分頁；**Esc** 結束搜尋 |
| 拖曳膠囊 | 移動面板。放到螢幕右邊緣就會變成邊緣細條 |

面板只顯示在第一個桌面空間（Space）。在其他空間與全螢幕 App 上會隱藏。

終端機裡也能用同樣的搜尋：`./run.sh search [--chat] [--closed] 關鍵字`。

## 規則：分頁何時隱藏、何時回來

背景程式每秒檢查一次。

- **回來：** 代理在**等你**，或已**完成**但你還沒看過。代理結束時也會回來。
- **隱藏：** **已完成**（看過）或**閒置**的分頁，在 20 分鐘後。**工作中**的分頁只會因下方的 N 上限而隱藏（設定 `daemon.working_park_s` 可讓它在你離開該秒數後也隱藏）。
- **分頁太多：** 顯示的代理分頁超過 N（5）個時，先隱藏閒置的，再來是已完成的，最後是工作中的。
- **永不隱藏：** 你正在用的分頁、每個視窗目前選取的分頁、等待中的分頁、釘選的分頁、視窗的最後一個分頁、有分割窗格的分頁。
- **打字時不打擾：** 自動叫回分頁前，會等到 3 秒內沒有按鍵。之後，你打字的視窗會拿回鍵盤焦點。

安裝前就啟動的代理分頁，會在 5 秒內依程式名稱（`claude`、`codex`）被找到。

其他分頁（shell、SSH、其他程式）完全不會被動到。

## 設定

把 `config.example.json` 複製到 `~/.agent-hover/config.json`，再修改需要的項目：

```json
{
  "panel":  {"font_size": 12, "list_width": 440, "hover_ms": 120,
             "hotkey": "ctrl+opt+space", "fold_idle_h": 12, "fold_waiting_h": 24, "closed_days": 7,
             "dock_px": 4, "dock_delay_ms": 500, "magnet_px": 24},
  "daemon": {"n_active": 5, "idle_min": 20, "working_park_s": 0,
             "cpu_hot_pct": 50, "cpu_hot_min": 2, "notify_limit_reset": true},
  "agents": {"extra": []},
  "remote": {"hosts": []},
  "update_check": false
}
```

- `panel` 的變更 1 秒內生效。`daemon` 的變更要執行 `./run.sh stop` 與 `./run.sh start --apply` 後才生效。
- 命令列參數會覆蓋設定檔，例如 `./run.sh start --apply --n 8`。
- `panel.hover_ms`：游標要在膠囊或邊條上停留這麼久（毫秒）清單才展開，只是經過的游標不會打開它。預設 120。`0` 表示立刻展開。
- `panel.hotkey`：在任何 App 中按下，以搜尋模式打開清單，在其他桌面空間也可以。再按一次關閉。修飾鍵：`ctrl`、`opt`、`cmd`、`shift`；按鍵：字母、數字、`space`、`return`、`tab`、`f1`–`f12` 與部分標點。`"off"` 表示關閉。如果沒有反應，很可能有其他 App 用了同一組按鍵（例如 Alfred 或 Raycast 的 `opt+space`）。
- `panel.fold_idle_h`：已完成或閒置超過這麼多小時的分頁，會收合成清單最後的一行。游標停在這一行上，或點它，就會展開。收合的分頁不計入膠囊上的數字。釘選的分頁、新的（還沒看過的）分頁與搜尋結果不會收合。`0` 表示關閉。
- `panel.fold_waiting_h`：問題超過這麼多小時沒有回覆（被遺忘的對話），也會收合，且不再決定邊緣細條的顏色，也不計入膠囊上的數字。預設 24。`0` 表示等待中的分頁永遠不收合。
- `panel.closed_days`：搜尋時也列出最近 N 天關閉的代理（只算真正的對話，不含 `claude -p` 或 `codex exec`）。點一下：新分頁會執行 `cd <資料夾>`，並輸入 `claude --resume <id>`（或 `codex resume <id>`），但不按 Enter。指令會沿用該對話啟動時的選項（例如 `--model`、`--effort`、`--permission-mode`、`--dangerously-skip-permissions`，Codex 的 `-m`、`-s`、`-c`），並去掉提示詞、`--continue`/`--resume`、對話 ID、`--name` 與 `--worktree`。背景程式把每個啟動指令存在 `~/.agent-hover/commands.json`（保留 60 天）。背景程式沒看過就結束的對話，使用不帶選項的指令。`0` 表示關閉。
- `panel.dock_px`、`panel.dock_delay_ms`：螢幕邊緣有多「黏」。拖曳中的面板要推過右邊緣至少 `dock_px`，並停留 `dock_delay_ms`（此時出現藍框），放開才會變成邊緣細條。稍微拉回就會變回面板。
- `panel.magnet_px`：拖曳中的面板離螢幕邊緣在這個距離以內時，會貼齊邊緣（從邊緣細條拉出來後也一樣）。拖曳時按住 ⌥（Option）就不吸附。`0` 表示關閉。
- `daemon.working_park_s`：你離開工作中的分頁這麼多秒後就隱藏它。預設 `0`：只有在可見的代理分頁超過 `n_active` 時，工作中的分頁才會隱藏（且最後才輪到它）。
- `daemon.cpu_hot_pct`、`daemon.cpu_hot_min`：代理沒在工作，但 CPU 用量至少這個百分比、持續這麼多分鐘時，顯示紅色火焰。`cpu_hot_pct: 0` 表示關閉。
- `daemon.notify_limit_reset`：Codex 分頁因用量上限而等待時，把滑鼠停在它的代理標記上，即可看到重置時間。到了重置時間，macOS 會跳出通知，告訴你代理可以繼續（Codex 不會自己繼續）；之後你第一次展開清單時，該代理標記會閃爍 30 秒，或直到你在面板上點一下。`false` 表示不通知。第一次通知時，系統可能會要求你允許 Script Editor 的通知。
- `update_check`：設為 `true` 時每天檢查一次新版本。有新版時膠囊會顯示 **↑ update**，`./run.sh doctor` 也會提示。執行 `./run.sh update` 即可更新。預設關閉。
- `~/.agent-hover/log/` 裡的紀錄檔超過 5 MB 會改名為 `<名稱>.1`，只保留一個舊檔。
- `agents.extra`：要額外辨識的程式名稱（例如 `qwen`）。這類代理也需要設定 hook，以 Claude 格式的事件呼叫 `agent_hover/hook.py <名稱>`。
- 代理標記：`~/.agent-hover/agents.json`，例如 `{"codex": {"mark": "◇", "color": "#10A37F"}}`。
- `remote.hosts`：見[遠端主機](#遠端主機)。預設為空：不會連線。

## 遠端主機

在另一台機器（例如 GPU 伺服器）的 tmux 裡執行的代理，可以和本機分頁列在同一個面板。那台機器上不安裝任何東西：沒有檔案、沒有 hook、沒有背景程式，也不改設定。

**設定步驟：**

1. 確認 `ssh <主機>` 用金鑰即可登入，不會詢問密碼。
2. 在 `~/.agent-hover/config.json` 加入主機：

   ```json
   "remote": {"hosts": [
     {"name": "gpu-box", "connect": ["ssh", "gpu-box"]}
   ]}
   ```

3. 測試：`./run.sh remote check gpu-box`，會列出找到的代理窗格。
4. 重新啟動背景程式：`./run.sh stop && ./run.sh start --apply`。

**主機設定：**

| 鍵 | 意義 |
|---|---|
| `name` | 必填。面板上顯示的名稱 |
| `connect` | 必填。在主機上執行 shell 指令的命令：要執行的指令會加在最後一個參數。任何連線方式都可以：`["ssh", "-p", "2222", "me@10.0.0.5"]`、`["ssh", "-J", "jump", "gpu-box"]`。沒有 `connect` 的主機會被略過 |
| `attach` | 點一下某列時，輸入到新本機分頁的指令。會填入 `{pane}`、`{session}`、`{window}`、`{host}`。ssh 的 `connect` 預設為：`ssh -t gpu-box 'tmux attach -t {pane} \; select-window -t {pane} \; select-pane -t {pane}'` |
| `interval_s` | 每次掃描間隔秒數。預設 5 |
| `share_connection` | `false`：不共用 ssh 連線（見下文）。預設 `true` |
| `enabled` | `false`：略過這台主機 |

**在主機上執行什麼：** 每 `interval_s` 秒，背景程式透過 `connect` 執行一段唯讀的 shell 腳本。`./run.sh remote script` 會印出它。它只讀取：

- `tmux list-panes`：窗格、所在視窗與標題。
- `ps`：哪些窗格在執行 `claude` 或 `codex`，以及 CPU 與記憶體。每行指令只送出前兩個字。
- `~/.claude/sessions/*.json`：Claude Code 為每個執行中 session 寫的狀態檔（busy、idle 或 waiting；資料夾；tmux 窗格）。
- 每個執行中 Codex session 檔（`~/.codex/sessions`）的最後 64 KB，以及每分鐘一次各代理對話紀錄中的模型名稱。
- `tmux capture-pane -p`：每個 Codex 窗格最後 30 行非空白的畫面。Codex 等待核准時不寫任何檔案。窗格標題為「Action Required」，或底部幾行出現核准、提問、信任或更新提示時，該列顯示 **waiting**，並以該提示為問題。提示文字取自 [herdr](https://github.com/herdrdev/herdr) 的 Codex 偵測規則（Apache-2.0）。

懸停預覽執行 `tmux capture-pane -p`。只有點擊會改變東西。若已有本機分頁連到該 tmux session，就在那裡選取該列的 tmux 視窗與窗格（`tmux select-window`、`select-pane`）；否則開一個執行 `attach` 的本機分頁。你自己連上的分頁，依 ssh 連線找出：背景程式執行 `tmux list-clients`，讀取每個 client 的 `SSH_CONNECTION`（Linux 主機），再與本機 ssh 的連接埠（`lsof`）比對。經由 ControlMaster 的 ssh 分頁，使用 master 的連線。多個分頁共用同一條連線時，選 ssh 啟動時間與遠端 ssh session 相同的那個分頁。背景程式的紀錄（`./run.sh log`）會記下啟動時的腳本，以及每次 peek 與 select 指令。

**共用一條連線：** 對 ssh 的 `connect`，背景程式會加上 `BatchMode=yes`（不詢問密碼）、`ConnectTimeout=5`、`Compression=yes` 與共用連線（`ControlMaster=auto`、`ControlPersist=120`，控制 socket 放在 `~/.agent-hover/`）。只有你的指令沒設定該選項時才會加。第一次掃描登入一次；之後的掃描都重用這條連線（約 0.1 秒，而不是 1–2 秒）。這是 OpenSSH 用戶端的功能，伺服器不需要任何改動。Windows 的 OpenSSH 不支援：請設 `"share_connection": false`。

**和本機分頁（hook）相比的限制：**

- 狀態變化最多晚 `interval_s` 秒才顯示。
- Claude：busy、idle、waiting 來自 Claude 的狀態檔。Claude 不會寫「完成」：背景程式看到代理從工作中變成閒置時，會顯示為**完成**，直到你看過它。比一次掃描還短的回合可能漏掉。因用量上限或登入問題而停止時，會顯示為閒置，列上也沒有問題文字（預覽會顯示畫面）。
- Codex：工作中、完成、錯誤、中斷與用量上限（含重設時間）來自 session 檔的結尾。Codex 等待核准時可能顯示為工作中（未驗證）。
- 只顯示 tmux 窗格裡的代理。Codex 的 session 檔透過 `/proc`（Linux）或 `lsof` 找到。
- 遠端列不會被隱藏或移動：本機沒有可以隱藏的分頁。

## 指令

| 指令 | 作用 |
|---|---|
| `./run.sh` 或 `./run.sh up` | 啟動背景程式（`--apply`）與面板 |
| `./run.sh down` | 停止面板與背景程式（會先叫回它隱藏的分頁）。也會暫停自動啟動，直到下次 `up` |
| `./run.sh doctor` | 檢查設定，並為每個問題提供修正方法 |
| `./run.sh update` | 取得新版本（`git pull --ff-only`、`uv sync`），並重新啟動正在執行的背景程式與面板 |
| `./run.sh autostart on` / `off` / `status` | 登入時與 iTerm2 執行時自動啟動（LaunchAgent）；`off` 會移除 |
| `./run.sh start [--apply]` | 只啟動背景程式。沒有 `--apply` 時是試跑 |
| `./run.sh stop` | 只停止背景程式。停止前會叫回它隱藏的所有分頁 |
| `./run.sh status` | 模式、隱藏數量、最後幾行記錄 |
| `./run.sh restore` | 叫回它隱藏的所有分頁（當機後也可用） |
| `./run.sh log` | 持續顯示決策記錄 |
| `./run.sh panel` / `panel-stop` | 啟動／停止面板 |
| `./run.sh search [--chat] [--closed] 關鍵字` | 依名稱、資料夾、對話內容找代理分頁；`--closed` 會加上最近關閉的代理與恢復指令 |
| `./run.sh remote script` / `remote check 名稱` | 印出遠端主機用的唯讀腳本／在主機上掃描一次並列出代理窗格 |

## 會改什麼，以及如何復原

- **Hook：** 在 `~/.claude/settings.json` 加入有標記的項目，在 `~/.codex/config.toml` 加入有標記的區塊，在 `~/.gemini/config/hooks.json` 加入 `agent-hover` 項目（有安裝 agy 且檔案不存在時會建立；還原時會再刪除）。這些檔案的其他內容不會變。安裝前會先備份到 `~/.agent-hover/backup/<時間>/`。
- **agy：** Antigravity 的 agy CLI、Antigravity IDE 與 app 都讀 `~/.gemini/config/hooks.json`。在 IDE 與 app 裡，hook 找不到 iTerm2 分頁，因此不做任何事。它對 PreToolUse 一律回答 `allow`，所以仍由 agy 自己的權限規則決定。
- **資料：** `~/.agent-hover/` 存放狀態檔、記錄、釘選與備份。只有你的使用者帳號能讀取。
- **自動啟動（只有你開啟時）：** `~/Library/LaunchAgents/local.agent-hover.plist`。
- **iTerm2：** 它會隱藏與叫回代理分頁，並把叫回的分頁移回原位。iTerm2 內建的 Claude Code 整合不會被更動。

復原方式：

```sh
./uninstall.sh                        # 試跑：顯示會移除什麼
./uninstall.sh --apply                # 移除自動啟動、停止、叫回所有分頁、只移除本工具的 hook
./uninstall.sh --restore-backup       # 試跑：還原安裝前的原始檔案
./uninstall.sh --restore-backup --apply
```

**注意：** 搭配 `--restore-backup` 使用 `--force` 前請先讀這段。如果檔案在安裝後有變動，還原會拒絕執行，以保護之後的修改。`--force` 會強制還原，那些修改會遺失。

## 隱私

所有資料都留在你的 Mac 上，不會連網；例外只有：

- 你開啟的更新檢查（`"update_check": true`）：每天一次用 `git ls-remote` 向 GitHub 查詢最新的 commit ID。它只顯示標記，不會下載或安裝。
- 你加入的遠端主機（`"remote"`）：透過你自己的 `connect` 指令做唯讀掃描（見[遠端主機](#遠端主機)）。結果只留在記憶體與 `~/.agent-hover/panel.json`。

- hook 會儲存每個代理的狀態、資料夾，以及待回覆問題的文字。
- 對話搜尋與小框中的模型名稱會讀取代理自己的對話紀錄檔（`~/.claude/projects`、`~/.codex/sessions`），只在你搜尋時，或針對目前開著的分頁讀取。
- 對話搜尋會在 `~/.agent-hover/chatcache/` 保存一份訊息文字的副本（不含工具輸出），所以一次搜尋遠少於 0.1 秒。對話紀錄檔被刪除後，下次對話搜尋時會一併移除它的副本。這個資料夾可以隨時刪除，下次搜尋會重新建立。
- 對還沒有 hook 事件的代理分頁，背景程式會讀取 Claude Code 自己的程序檔（`~/.claude/sessions/<pid>.json`），取得對話 ID、資料夾與狀態。它也會讀取程序清單（`ps`）來計算 CPU 與記憶體。
- Claude Code 會透過 hook 回報自己的用量上限：一輪因用量上限或帳號問題停止（`StopFailure`），或上限在 Mac 睡眠時重置（`quota_auto_resume_stale`），都會顯示為**等待中**，並附上 Claude 的訊息。
- 對工作中的 Codex 分頁，背景程式每 5 秒讀取其對話紀錄檔（`~/.codex/sessions`）的結尾。Codex 在一輪因錯誤結束時不會送出 hook，所以要靠這個方法，把用量上限的停止顯示為**等待中**（Codex 接著會詢問是否改用較小的模型）。還沒有 hook 事件的 Codex 分頁，會透過其程序開啟的檔案（`lsof`）對應到它的對話紀錄檔。
- 較新的 Codex 版本在 `codex app-server` 背景程式中執行 hook，而不是在分頁裡的 `codex` 底下。該背景程式保留啟動它的那個分頁的環境，所以 `$ITERM_SESSION_ID` 指向錯誤的分頁。hook 會看出其父程序是 `codex app-server`，改用對話 ID 找分頁（紀錄中有該 ID 的已開啟 Codex 分頁），否則依資料夾找：同一資料夾的已開啟 Codex 分頁，優先選還沒有對話的分頁。同一資料夾有兩個新的 Codex 分頁時，略過該事件（不猜測），並在 `events.jsonl` 記為 `unrouted`。
- 偷看功能透過 iTerm2 讀取滑鼠所指分頁的畫面，並把最後幾行存在 `~/.agent-hover/peek.json`。

## 運作方式

```
Claude Code / Codex hooks ──► ~/.agent-hover/state/<分頁>.json
                                        │
                     背景程式（iTerm2 Python API，每秒一次）
                       │ 規則：隱藏／叫回             │ 寫入 panel.json
                       ▼                             ▼
                 iTerm2 分頁                  浮動面板（Swift）
                                             點擊 ──► requests/ ──► 背景程式
```

| 檔案 | 用途 |
|---|---|
| `agent_hover/hook.py` | hook 本體。只用標準函式庫，一律以 0 結束，不輸出任何內容 |
| `agent_hover/states.py` | hook 事件 → 狀態；狀態檔；問題文字 |
| `agent_hover/rules.py` | 何時隱藏與叫回（純邏輯） |
| `agent_hover/daemon.py` | iTerm2 背景程式 |
| `agent_hover/search.py` | 依名稱、資料夾、對話搜尋；從對話紀錄讀出模型 |
| `agent_hover/config_edit.py` | 安裝、移除、還原 hook 項目 |
| `panel/AgentHoverPanel.swift` | 浮動面板 |

測試：`uv run pytest`。端對端測試 `tests/e2e_sandbox.py` 會開一個測試視窗與假的代理分頁。**請先停止背景程式**（`./run.sh stop`）。

## 限制

- 只支援 macOS 與 iTerm2。只有 iTerm2 能隱藏分頁並讓它繼續執行。
- 預設只支援 Claude Code、Codex 與 agy。
- agy 沒有「詢問權限」的 hook 事件。背景程式每 2 秒讀取每個工作中 agy 分頁的對話資料庫（`~/.gemini/antigravity-cli/conversations/<id>.db`，唯讀）：若有步驟標記為等待，該列會顯示為**等待中**，並以指令作為問題。這依賴 agy 的內部格式，日後可能改變。`agy -p` 的執行（例如另一個代理在它的分頁裡啟動的審查）會被忽略。
- 遠端代理只在 tmux 裡執行、且主機列在 `remote.hosts` 時才顯示。它們的狀態用輪詢取得，不如 hook 精確（見[遠端主機](#遠端主機)）。
- 有分割窗格的分頁會列出，但不會被隱藏或移動。
- 面板與背景程式依賴 iTerm2 與 macOS 的行為，未來版本可能改變。例如空間檢查使用 macOS 私有呼叫；若失敗，面板就會在每個空間顯示。

## 相關專案

很多工具都能顯示代理狀態。就我們所查（2026 年 10 月），沒有一個會隱藏並叫回 iTerm2 分頁。請依你的使用方式挑選：

| 工具 | 介面 | 代理 | 隱藏分頁 | 說明 |
|---|---|---|---|---|
| **本工具** | 浮動面板 + iTerm2 分頁管理 | Claude Code、Codex | **會**（顯示 N 個，其餘隱藏） | 不改分頁標題；你打字時會等待 |
| [iTerm2 3.7 內建](https://iterm2.com/claude-code-integration.html) | iTerm2 的 Session Status | Claude Code | 不會 | 原生功能；狀態點、說明列、點擊跳轉。本工具可與它並存 |
| [Megadesk](https://github.com/saugon/megadesk) | 浮動面板 + 選單列 | Claude Code、Codex | 不會 | 可聚焦 iTerm2 與 Ghostty 的分頁或窗格；快捷鍵；PR/CI 追蹤 |
| [cc-status-bar](https://github.com/usedhonda/cc-status-bar) | 選單列 | Claude Code、Codex | 不會 | 可自動聚焦等待中的終端機；快捷鍵跳到最緊急的工作階段 |
| [CodeIsland](https://github.com/wxtsky/CodeIsland)、[Notchi](https://github.com/sk-ruban/notchi) | MacBook 瀏海 | 多種 | 不會 | 可在瀏海上核准或回答 |
| [cmux](https://github.com/manaflow-ai/cmux) | 自己的終端機（基於 Ghostty） | 任何 | — | 垂直分頁、通知光圈。適合想換新終端機的人 |

代理事件的設計參考了 [agent-board](https://github.com/WenyuChiou/agent-board)（給多個代理共用的進度看板）。

## 授權

MIT。請見 [LICENSE](LICENSE)。
