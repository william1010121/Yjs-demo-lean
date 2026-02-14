# Yjs-demo-lean

一個以 **Yjs 協作編輯**為基礎、整合 **Lean 4 Language Server (`lake serve`)** 的多人即時編輯器。  
目標是讓多位使用者同時編輯 `Scratch.lean`，並在瀏覽器中看到 Lean 的診斷、goal、hover、補全等資訊。

## 1. `run.py` 使用方式與參數

## 先決條件

1. Python 3.10+（建議）
2. 已安裝 Lean 4 / elan，且 `lake` 在 PATH 中可用

## 安裝（第一次）

```bash
python -m pip install -r requirements.txt
```

## 啟動

```bash
python run.py
```

預設會做 setup（安裝 Python 套件、`lake update`、`lake build`）再啟動伺服器，預設網址為 `http://localhost:8080`。

## 參數總覽

```bash
python run.py [--port PORT] [--skip-setup] [--with-mathlib]
```

### `--port`

- 說明：指定 HTTP/WebSocket 監聽埠
- 預設：`8080`
- 範例：

```bash
python run.py --port 3000
```

### `--skip-setup`

- 說明：跳過 setup 步驟（不跑 `pip install`、`lake update`、`lake build`）
- 適用：你已經 setup 過，想快速重啟服務
- 範例：

```bash
python run.py --skip-setup
```

### `--with-mathlib`

- 說明：建立/覆寫 `lean-project/lakefile.lean` 時加入 mathlib 依賴，並在 setup 時執行 `lake exe cache get`
- 影響：第一次會明顯較慢，但可直接使用 mathlib
- 範例：

```bash
python run.py --with-mathlib
```

### 常用組合

```bash
# 第一次完整啟動（含 mathlib）
python run.py --with-mathlib

# 後續快速啟動
python run.py --skip-setup

# 其他埠 + 快速啟動
python run.py --port 3000 --skip-setup
```

---

## 2. 系統整體邏輯（含 WebSocket）

這個系統有兩條 WebSocket 通道，分工明確：

1. **Yjs WebSocket**：同步多人編輯內容
2. **LSP WebSocket**：前端與 `lake serve` 之間的 JSON-RPC 溝通

### 2.1 元件與責任

1. 瀏覽器前端（`index.html`）
- CodeMirror 編輯器 + Yjs 協作
- Lean LSP client（initialize/didOpen/didChange/hover/completion）
- 顯示 diagnostics、goal、hover、file progress

2. Python 伺服器（`server.py`）
- 同時提供 HTTP 與兩種 WebSocket 路由
- Yjs：透過 `pycrdt-websocket` 管理 room 與持久化檔案
- LSP：為每個 session 啟一個 `lake serve` 子程序，負責轉發 LSP 訊息

3. Lean 專案（`lean-project`）
- 真正被 Lean 分析的工作目錄
- 主要檔案：`lean-project/src/Scratch.lean`

### 2.2 路由與協定

1. `GET /`
- 回傳前端頁面 `index.html`

2. `GET /file-uri`
- 回傳：
  - `fileUri`：`Scratch.lean` 的絕對 `file://` URI
  - `rootUri`：`lean-project` 的絕對 `file://` URI

3. `WS /yjs/{room}`
- 用於 Yjs 協作同步
- 房間內容持久化於 `data/{room}.ystore`

4. `WS /lsp/{session_id}`
- 用於 LSP JSON-RPC 訊息
- 伺服器端會為該 session 啟動/管理對應的 `lake serve`

### 2.3 資料流程（編輯到診斷）

1. 使用者在 CodeMirror 輸入內容
2. Yjs 把內容同步給其他協作者（`/yjs/{room}`）
3. 前端 debounce 後送 `textDocument/didChange` 到 `/lsp/{session_id}`
4. `server.py` 把 LSP 訊息用 `Content-Length` framing 轉發到 `lake serve`
5. 同時伺服器會把最新內容寫回 `lean-project/src/Scratch.lean`
6. `lake serve` 回傳 `publishDiagnostics` / hover / completion / goal
7. 前端更新 lint 標記、Infoview、進度條

---

## 3. 相比 `Yjs-demo` 新增了哪些功能

## 核心升級

從「一般協作編輯器」升級為「Lean 4 協作 IDE（瀏覽器版）」。

## 主要新增點

1. **Lean LSP 整合**
- 新增獨立 LSP WebSocket 路徑 `/lsp/{session_id}`
- 前端可做 `initialize`、`didOpen`、`didChange`、`hover`、`completion`

2. **每 session 獨立 `lake serve`**
- 後端有 process manager，控制啟動、重用、關閉

3. **Infoview 面板**
- 右側顯示 goal、hover、當前行的診斷訊息

4. **Diagnostics 視覺化**
- 將 LSP 診斷套入 CodeMirror lint gutter 與底線提示

5. **LSP 自動補全**
- 使用 `textDocument/completion` 填入 CodeMirror 補全來源

6. **Lean 專案初始化自動化**
- `run.py` 支援 setup 流程（`pip install`、`lake update`、`lake build`）
- 支援 `--with-mathlib` 一鍵切換到 mathlib 環境

7. **可持續的 Lean 檔案落盤**
- `didOpen` / `didChange` 會同步寫入 `Scratch.lean`

8. **前端 UI 升級**
- 從簡單 textarea / 基礎 CodeMirror，升級為雙面板 IDE 風格介面

---

## 4. 專案架構

## 目錄結構

```text
Yjs-demo-lean/
├── index.html                  # 前端主頁（CodeMirror + Yjs + LSP + Infoview）
├── server.py                   # HTTP + Yjs WS + LSP WS 伺服器
├── run.py                      # 啟動腳本（含 setup 與參數）
├── requirements.txt            # Python 依賴
├── BUG-HANDOFF.md              # CodeMirror/esm.sh 問題交接文件
├── data/                       # Yjs 房間持久化資料
│   └── yjs/
│       └── *.ystore
└── lean-project/               # Lean 4 專案根目錄
    ├── lakefile.lean
    ├── lean-toolchain
    ├── lake-manifest.json
    └── src/
        └── Scratch.lean
```

## 模組分層

1. **Presentation Layer**
- `index.html`
- 負責編輯器互動、協作狀態、Infoview 呈現

2. **Transport Layer**
- `server.py` 的兩個 WebSocket 路由
  - `/yjs/{room}`：CRDT 協作同步
  - `/lsp/{session_id}`：LSP JSON-RPC 轉發

3. **Language Intelligence Layer**
- `lake serve`（由 `server.py` 啟動）
- 提供 Lean 語意分析、錯誤、goal、補全

4. **Persistence Layer**
- Yjs：`data/*.ystore`
- Lean source：`lean-project/src/Scratch.lean`

## 架構圖（邏輯）

```text
Browser (CodeMirror + Yjs + LSP Client)
   ├─ WS /yjs/{room} ───────────────┐
   └─ WS /lsp/{session_id} ───────┐ │
                                   │ │
Python Server (Starlette + Hypercorn)
   ├─ Yjs via pycrdt-websocket     │ │
   │   └─ persist to data/*.ystore ◄─┘
   └─ LSP Proxy (Content-Length framing)
       ├─ spawn/manage lake serve
       └─ sync file to Scratch.lean
                │
                ▼
         lean-project/ + lake serve
```

## 開發備註

1. 目前前端依賴使用 esm.sh + importmap，與 CodeMirror 套件版本相容性需特別注意。細節可看 `BUG-HANDOFF.md`。
2. 若你要把這個專案長期維護，建議未來改成本地 bundler（Vite/esbuild）以避免多份 CodeMirror module instance 問題。
