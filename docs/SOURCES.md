# 素材来源核查（2026-09-17）

## 豆瓣

豆瓣电影条目有海报/剧照页面，但本次访问示例海报页被重定向至安全验证，未取得稳定可解析页面。未能核实当前有可新申请、可稳定使用的官方电影素材 API；不能凭旧教程宣称可以申请。

海报通常仍含标题；未核实有可替代 TMDB logos/Fanart 的标准透明艺术字 Logo 接口，也不能保证无字背景或原始高分辨率。素材与清洗图片是两项能力，不应混淆。

官方声明对数据使用及采集设有限制，授权联系为 bd-team@douban.com。公开可见不代表可再分发。来源：https://www.douban.com/about/legal 。这是来源使用条件摘要，不是法律意见。

提供 `app/scripts/douban_candidates.py` 作为可选单页采集工具：只接受指定电影条目/海报/剧照页，先检查 robots，有限请求、缓存、不递归爬取；发现验证码、登录要求、403/429 或站外跳转即停止。不复制登录 Cookie、不换代理重试、不使用共享 key。输出候选图片 URL 与来源 JSON，不批量下载或发布海报，不提供绕过限制的选项。

```powershell
.\.venv\Scripts\python.exe app\scripts\douban_candidates.py "https://movie.douban.com/subject/1292052/photos?type=R" --output data\douban-candidates.json
```

在当前验证环境未证明在线采集可用。可对自己有权处理的已保存页面使用 `--html 文件路径` 进行离线解析，输出仅为候选，须人工核对。

## 替代方案

TVmaze 提供免费公开电视剧 API 和图片端点，已在 V0.3 接入；电影/中文艺术字覆盖不能保证。来源：https://www.tvmaze.com/api 。本地图片和透明 Logo 导入不依赖外部 API。

## TMDB 申请与 GitHub

真实公开项目仓库可用于说明项目用途，但不能保证 TMDB 接受该网址或批准申请，也不代替实际用途授权。不要填不存在的网址或虚构公司/联系方式。

申请用途可准确表述：A locally hosted, open-source poster normalization tool with a web interface. It optionally retrieves movie/TV metadata and image candidates for user review. Image editing and inference run locally. API credentials remain on the user's machine.

是否为商业用途按实际部署目的填写，不以“源码开源”代替判断。取得凭据后，当前应用需要的是 API Read Access Token。不要在公开仓库、Issue 或截图中贴 token。

官方申请及使用条件：https://developer.themoviedb.org/docs/faq 。TMDB 要求来源署名与批准的 Logo；正式启用时应完成界面的署名，不能仅在 README 中声明就认为已满足全部要求。
