# orca-traditional-chinese

<!-- orca-badge:start -->
[![Orca v1.4.221 · zh-TW 100%](https://img.shields.io/badge/Orca-v1.4.221_%C2%B7_zh--TW_100%25-2ea44f)](https://github.com/stablyai/orca/releases/tag/v1.4.221)
<!-- orca-badge:end -->

[Orca](https://github.com/stablyai/orca) 的繁體中文（台灣）語言包，涵蓋設定、側邊欄、編輯器、終端機、整合服務與系統選單等介面。

## 現況

<!-- sync-status:start -->
- **14,948 / 14,948** translatable strings translated (100%)
- Synced against `stablyai/orca` `v1.4.221` (`9dd8812`, 2026-10-05)
- 180 plugin-protected keys and 2 oversize inline-CSS keys are excluded by design and fall back to English
<!-- sync-status:end -->

| 項目 | 說明 |
|---|---|
| Plugin | `endeavoryen.traditional-chinese` |
| 相容下限 | `engines.orca` `>=1.4.0`（徽章標示為已對齊的釋出版本） |
| 本地化原則 | 台灣繁體慣用語（非簡中直譯） |

譯文採 LLM 輔助生成，並經由語彙庫、變數佔位符與載入器等自動化檢查。歡迎共同維護，詳見[貢獻](#貢獻)。

## 安裝

1. **新增來源**：前往 **Settings → Plugins → Add marketplace source**
   - Git URL：`https://github.com/EndeavorYen/orca-traditional-chinese.git`
   - Git ref：`main`
2. **安裝外掛**：在 Marketplace 中安裝 **Traditional Chinese Language Pack**
3. **套用語言**：前往 **Settings → Appearance → Language**，選取 **繁體中文（台灣）**

> **遷移提示**：若先前使用 `a-lang.traditional-chinese`，因發布者身分已變更為 `endeavoryen`，請先自外掛管理介面移除舊版來源與外掛，再依上述步驟安裝。

## 保持同步

上游 `en.json` 更新頻繁。本專案持續追蹤最新 **stable** release（排除 `-rc` 與 `main` 分支），並由每日 CI（`resync-check`）偵測落後狀況。

執行同步：

```sh
claude          # 接著執行：/resync
```

僅查看差異、不更動檔案：

```sh
python3 scripts/sync.py check
```

完整流程與自動化細節請參閱 [docs/MAINTAINING.md](docs/MAINTAINING.md)。

## 維護指令

| 指令 | 說明 |
|---|---|
| `python3 scripts/sync.py check` | 比對最新 stable release 差異；落後則 exit 1 |
| `python3 scripts/sync.py prepare` | 擷取待翻譯項目並建立 `work/todo/` 批次檔 |
| `python3 scripts/sync.py apply` | 合併 `work/done/` 譯文、驗證並更新版本鎖定檔與文件 |
| `python3 scripts/sync.py validate [--strict]` | 執行與 CI 相同的語法與格式驗證 |
| `python3 -m unittest discover -s tests` | 執行測試套件 |

### 目錄結構

| 路徑 | 說明 |
|---|---|
| `locales/zh-TW.json` | 繁體中文譯文字串 |
| `orca-plugin.json` | 外掛設定檔（Manifest） |
| `orca-marketplace.json` | Marketplace 發布清單 |
| `scripts/` | 同步、驗證與術語庫工具 |
| `.sync/` | 同步鎖定記錄與英文保留清單（keep-english） |
| `docs/MAINTAINING.md` | 維護指南與作業說明 |

## 已知限制

- **外掛保護鍵值**（`auto.components.settings.plugin*`）：依上游架構設計不可覆寫，維持英文。
- **超長字串**（超過 8,192 字元，如 inline CSS）：不收入語言包，自動回退為英文。
- **位置參數句型**：英文以 placeholder 動態注入詞彙，中文語序不同時已盡可能調整為自然語句，但仍保留既有 placeholder 結構。
- **介面顯示名稱**：語言選單目前顯示為 `zh-TW — endeavoryen.traditional-chinese`（上游格式）。原生自訂名稱支援追蹤於 [#13031](https://github.com/stablyai/orca/issues/13031) 與 [#13140](https://github.com/stablyai/orca/pull/13140)。**在 PR #13140 合併前，切勿於 `orca-plugin.json` 加入 `displayName`**（上游 schema 設定為 `.strict()`，未辨識鍵值會導致整個外掛載入失敗）。

## 貢獻

歡迎針對 `locales/zh-TW.json` 提交 Pull Request。為確保翻譯一致性與外掛穩定度，請遵循以下規範：

- 維持既有鍵值結構，遵循台灣在地用語（請參閱 `scripts/glossary.json`）。
- 完整保留 `{{placeholder}}`、`{single}` 與 `<angle>` 等變數語法。
- 品牌名稱、代碼與 CLI 指令保持原文。

提交前請於本地執行驗證：

```sh
python3 scripts/sync.py validate
```
