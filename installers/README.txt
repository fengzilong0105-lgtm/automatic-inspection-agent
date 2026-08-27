本目录用途：

1) Windows 离线 Python 安装包（源码部署用）
   python-3.12.10-amd64.exe
   - Python 3.12.10 官方 Windows 64 位安装程序
   - 安装时请勾选「Add python.exe to PATH」
   - 静默安装：python-3.12.10-amd64.exe /passive PrependPath=1 Include_test=0
   - 详细步骤见项目根目录 README.md「新电脑从零部署」章节

2) 桌面应用安装脚本（交付给业务用户）
   SteadyOps.iss
   ChineseSimplified.isl  — 安装向导简体中文语言包（随仓库提供，无需拷进 Inno 安装目录）
   - Inno Setup 6 脚本，将 dist\SteadyOps\（PyInstaller onedir）打成 Setup
   - 构建：.\scripts\build.ps1 -Installer
   - 需本机安装 Inno Setup 6，或设置环境变量 INNO_SETUP_ISCC 指向 ISCC.exe
   - 方案说明见 docs\desktop-delivery-roadmap.md
