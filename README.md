# Poster Normalizer / 海报规范化工作台

Windows 本地 WebUI，用于海报原标题选区、局部修补、Logo 搬移、统一排版和批量人工审核。不使用付费生成式 AI API；推理模型按需下载到独立目录。支持可信局域网工作组访问。

当前基线 V0.3.2，仍是测试版本。本版修复蒙版透明擦除，新增独立标题工作室与分类素材检索，详见 [更新与升级说明](docs/RELEASE_V0.3.2.md)。删除任务、整图缩放裁切与大模型修补仍未完成，不要将计划中的功能当作当前能力。

## 能力

- 多图/文件夹/ZIP 导入、任务队列、历史版本、人工审核和 ZIP 导出。
- 原图框选、提取蒙版、透明 Logo、标题位置与尺寸调整、OpenCV 修补。
- 独立标题库、透明背景精修、擦除/恢复/撤销/重做、点选删除连通杂块、透明 PNG 导出和任务草稿保留。
- TMDB 透明标题与背景分类、Fanart 作品 ID 检索、官网链接及离线手工导入。背景图不保证无字。
- 本地 PP-OCRv5 检测与 LaMa ONNX 适配。GPU 可选 Windows DirectML，默认 CPU。
- 模型中心下载/续传/校验/独立目录；SAM、GroundingDINO、AnyText2 仅资产储备，未接入推理。
- 名称优先检索：同时查询 TVmaze 与已配置的 TMDB，汇总海报候选；选图后下载到本机、自动匹配横竖输出规格并直接进入编辑。个人凭据只存本机。

应用代码位于 `app`；`runtime`、`data`、`models` 是安装根目录下独立目录，不进入源码仓库。此源码仓库不附带模型、用户海报或离线安装二进制。

## 从源码启动（联网安装开发依赖）

需要 Python 3.13 x64（Windows）。在仓库根目录执行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r app\requirements.lock.txt
.\.venv\Scripts\python.exe app\launcher.py
```

使用控制台显示的访问码登录；不要把访问码、TMDB token 或 Fanart key 提交到仓库。仅部署于可信本机/内网，不直接暴露公网。

## 构建 Windows 离线包

源码中的 `start.cmd` 用于构建后的离线包，需要先准备 `offline` 目录。在有网络的 Windows/Python 3.13 环境执行：

```powershell
py -3.13 -m pip install packaging
py -3.13 -m pip download --only-binary=:all: --no-deps --platform win_amd64 --python-version 313 --implementation cp --abi cp313 -r offline\requirements-win313.lock -d offline\wheels
py -3.13 app\scripts\build_offline.py
py -3.13 app\scripts\package_release.py
```

构建脚本下载官方 Python 嵌入式运行时和 VC++ 安装程序、验证依赖闭包并生成文件哈希。安装阶段只使用随包文件。NVIDIA 驱动不包含在内。

## 测试与限制

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
cd app
..\.venv\Scripts\python.exe -m pytest -q
```

基线 15 项后端测试通过，核心模型 CPU 基础推理验证通过。Windows 安装、浏览器交互、4090 与 PR/ME 共存尚待实机验收；自动候选选择及修补结果需要人工检查。

## 素材与许可

应用原创代码采用 MIT；第三方依赖、模型和海报图片各自适用其许可，不受本仓库 MIT 自动授权。参见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。素材检索结果不代表权属已验证。

TMDB 为可选来源；本项目尚未获得 TMDB 背书或认证。This product uses the TMDB API but is not endorsed or certified by TMDB. 启用前请按实际用途确认访问权限，并按 TMDB 要求补齐品牌署名。开源代码不自动使商业使用变为非商业使用。

[豆瓣调研与受限采集工具](docs/SOURCES.md)：提供指定公开页的候选图片链接解析，遇到 robots 禁止、安全验证或限流会停止；不是稳定的豆瓣 API 替代品。工具未接入网页主流程。
