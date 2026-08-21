# SteadyOps 发布通道说明（方案 B 在线更新）

## 目录约定（当前内网）

服务器目录：

```text
/DATA1/download/steadyOps/
  SteadyOps-Setup-x.y.z.exe
  version.json
```

对应下载地址：

```text
http://106.120.201.126:14828/down/steadyOps/SteadyOps-Setup-x.y.z.exe
http://106.120.201.126:14828/down/steadyOps/version.json
```

## 打包时自动生成 version.json

```powershell
# 使用默认下载根地址（106.120.201.126:14828/down/steadyOps）
.\scripts\build.ps1 -Installer

# 自定义下载根 / 更新说明
.\scripts\build.ps1 -Installer `
  -UpdateBaseUrl "http://106.120.201.126:14828/down/steadyOps" `
  -ReleaseNotes "修复告警时间显示"
```

也可设置环境变量 `STEADYOPS_UPDATE_BASE_URL`（不要末尾 `/`）。

成功后会生成：

- `dist\SteadyOps-Setup-x.y.z.exe`
- `dist\version.json`（含 version / url / sha256 / notes）
- `releases\version.json`（同内容副本）

**无需再手动跑 Get-FileHash。** 把上述两个文件上传到 `/DATA1/download/steadyOps/` 即可。

## 客户端配置

桌面端 **设置 → 在线更新**：

- 版本源 URL：`http://106.120.201.126:14828/down/steadyOps/version.json`
- 勾选「启用在线更新检查」「启动时自动检查」
- 「检查更新」→「下载并安装」

配置写入 `%APPDATA%\SteadyOps\data\config.yaml` 的 `update:` 段。

## 发版检查清单

1. bump `pyproject.toml` 的 `version`
2. `.\scripts\build.ps1 -Installer`（可选 `-ReleaseNotes`）
3. 上传 `dist\SteadyOps-Setup-*.exe` 与 `dist\version.json` 到服务器目录
4. 用旧版客户端验证能检出并升级
