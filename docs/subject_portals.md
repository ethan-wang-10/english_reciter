# 语文、数学学科空间

## 站点与共享账号

- 英语：<https://english.itorange.online>
- 语文：<https://chinese.itorange.online>
- 数学：<https://math.itorange.online>

三个入口使用同一 Flask 后端、原有 `users.sqlite3` 和 `sessions.sqlite3`，继续沿用邀请码、账号启停、密码找回和家长关联规则。没有复制用户表或另建密码系统。新账号在任一学科注册后可登录另外两个学科，已有英语账号可直接使用。

新门户包括内置考卷、知识点梳理、学习记录和我的账号。年级采用上海五四学制：小学一至五年级，随后是预备班、初一至初三，以及高中三个年级。当前两个学科的目录为空，后续补充真实材料，不展示假考卷或虚构成绩。

账号管理继续使用原后台；密码找回沿用配置好的 `PUBLIC_BASE_URL`。在任一站退出会撤销该登录身份的全部会话，其他站下次请求时需重新登录。

## 一次登录与学科切换

英语站的“更多”增加语文、数学入口，新站顶部提供三个学科入口。已登录用户通过这些入口切换时，无需再次输入密码。

不同域名的 localStorage 彼此隔离，切换使用目标站发起的授权码流程：

1. 目标站生成随机 state 和 PKCE verifier，将 verifier 暂存在当前标签页的 sessionStorage。
2. 浏览器返回来源站，由来源站读取本域会话，通过 Authorization 请求一次性交换码。
3. 交换码绑定来源会话、目标站和 PKCE S256 challenge，有效期 60 秒，并以哈希保存。
4. 目标站校验 state，先从地址栏移除 code，再用 verifier 兑换同一账号的新会话。

长期登录令牌不进入 URL，回跳只允许明确配置的三个站点，交换码只能消费一次。来源退出、改密、账号停用或关联学生停用都会阻止后续兑换；家长身份和关联学生信息会继续保留。各站也保留普通用户名、密码登录。

## 内容和记录隔离

轻量学科接口不调用英语每日任务、背诵器、积分或英语考卷初始化：

| 接口 | 用途 |
| --- | --- |
| `GET /api/subjects/<subject>/bootstrap` | 当前身份、学科目录及概览 |
| `GET /api/subjects/<subject>/papers` | 学科考卷元数据 |
| `GET /api/subjects/<subject>/knowledge` | 学科知识点 |
| `GET /api/subjects/<subject>/records` | 当前学生的学科记录摘要 |

`subject` 只接受 `chinese`、`math`，接口均要求原有用户 token；家长读取关联学生数据。两个目录分别为 `subject_content/chinese/catalog.json` 和 `subject_content/math/catalog.json`，不混入英语内置卷。

记录预留在 `user_data_simple/<学生用户名>/subjects/<学科>/records.json`，没有文件时返回空列表，不创建英语任务或假记录。响应只包含摘要白名单字段，最多展示最近 200 条。当前先提供页面与内容目录，后续交互答题、阅卷和写入接口在该边界内补充。

## 构建和部署

`scripts/build_assets.py` 同时处理 `static/index.html` 和 `static/subject.html`，生成共享 manifest 和内容哈希资源。SSO 模板从 manifest 读取脚本地址。修改后执行 `python3 scripts/build_assets.py` 和 `python3 scripts/build_assets.py --check`。

根页面按 Host 选择学科，但不使用请求 Host 构造授权回跳地址。`/subjects/chinese/`、`/subjects/math/` 可在同一后端预览；完整跨域切换使用正式域名。测试可显式将 `app.config['SUBJECT_SITE_URLS']` 配置为三个本机端口，生产默认要求 HTTPS。

两个新入口代理到同一 `127.0.0.1:8000`，保留 Host。原语文独立工具按用户要求由新门户替换；切换前备份原 Nginx 配置和独立数据库，原项目移入服务器私有归档，移除公开入口。语文沿用已有证书，数学配置独立证书。

发布需验证 DNS、证书、`nginx -t`、主机路由、认证和三站切换。只安装审阅过的文件，保留线上独立改动、用户数据库和英语内容；新路由通过 Gunicorn 平滑重载激活，不迁移原用户表。
