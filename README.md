# LIMSData

独立的化验数据趋势查询系统。包含网页、Python接口、完整SQLite快照及可选SQL Server采集器，不依赖原MES项目。

## 数据

- 完整快照：**687,951条化验结果**，与源库汇总核对一致。
- 取样日期：**2019-10-24至2026-09-10**。
- 按6个自然月逐步向前回溯，检查2016-09-11至2026-09-11的20个窗口；源库当前所有记录都在此范围内。
- 77条记录缺少录入时间，按取样日期归入回溯窗口，原始字段仍保留为空。
- 数据库存储于 `seed/lims_history.sqlite3.gz`，压缩约49 MB，还原约362 MB。`seed/manifest.json`含SHA256和行数，启动时校验；现有运行库不会被覆盖。
- 默认采集天数140按录入时间计算；网页日期切片始终按取样日期查询。取样时间在源库仅精确到日。

本仓库包含公司化验数据，应保留为私有仓库。访问密码、SQL Server密码和本机凭据均不在仓库内。

## Docker部署（Linux服务器）

```bash
git clone git@github.com:drang7070/LIMSData.git
cd LIMSData
cp .env.example .env
# 编辑.env，设置LIMS_WEB_USERNAME和至少16字符的LIMS_WEB_PASSWORD
docker compose up -d --build
docker compose logs -f
```

服务默认监听服务器本机 `127.0.0.1:8080`，使用已有Nginx/Caddy和HTTPS域名反向代理至该地址。访问网页时输入 `.env` 配置的账号密码。容器内使用Waitress提供服务，不使用Flask开发服务器。

Nginx中已有HTTPS站点可添加：

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 300s;
    proxy_buffering off;
}
```

数据卷 `lims_data` 保存解压后的运行库。更新应用镜像不会覆盖运行库；不要删除数据卷。SQL Server访问不是启动前提，查询历史数据不需要公司VPN。

## 直接运行（Windows或Linux）

要求Python 3.12或更新版本：

```bash
python -m venv .venv
# Linux: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

本地打开 `http://127.0.0.1:8080`。直接运行默认仅本机访问。线上对外监听需要设置 `LIMS_ENV=production`、`LIMS_WEB_USERNAME`、`LIMS_WEB_PASSWORD`、`LIMS_HOST=0.0.0.0`，前端代理提供HTTPS。可用 `PORT` 修改端口，`LIMS_DATA_DIR` 指向持久化数据目录。

## 网页使用

装置、样品名、分析项、样品类型、单位支持联动和多选，取样日期含起止当天。筛选结果可收藏为URL，CSV按当前筛选流式导出全部明细。同单位同样品同分析项同日结果取平均；不同样品、单位不会混合平均。

“验收示例”选择2026-09-01至09-11、三排送、3P-1402、喹啉不溶物：真实源库仅一条，2026-09-07为 **1.53 % w/w**。选择2021-03-11至09-10可查看同一样品的18个历史测点。

原始结果和原始分析项保留；痕迹/少量/未检出沿用PBI的数值换算并标注。无效、不成线、文本和检出限不会伪造数值。大范围包含过多系列时，先选择装置、样品和分析项再查看图表。

## 可选：继续从公司源库采集

查询不需要以下组件。只有需要继续采集时，才安装SQL Server ODBC驱动、`pip install -r requirements-collector.txt`，并确保运行机器能够连接公司数据库网络。

连接默认值在 `config/lims.json`；可用 `LIMS_SERVER`、`LIMS_PORT`、`LIMS_DATABASE`、`LIMS_DRIVER`、`LIMS_AUTH`覆盖。SQL登录使用环境变量 `LIMS_USERNAME` 和 `LIMS_PASSWORD`。Windows也可运行 `python tools/lims_credentials.py`交互式加密保存；凭据不应提交到Git。

```bash
python tools/lims_collector.py --days 140 --date-basis DATEENTER
python tools/lims_backfill.py
```

第二条命令沿用 `data/lims_backfill_status.json` 的固定回溯任务，跳过已完成窗口。已附带完整十年回溯结果，因此再运行通常直接核对完成状态。后续近期数据使用第一条命令；更早历史不会被140天更新删除。

若确需网页按钮采集，设置 `LIMS_ENABLE_SYNC=true`。Docker默认没有安装ODBC驱动且关闭该功能。普通更新与历史回溯共用操作系统文件锁；网络故障不会覆盖已发布窗口。

## 验证

```bash
python bootstrap.py
python -m unittest discover -s tests -v
```

核对记录在 `seed/lims_backfill_acceptance.json`、`seed/lims_backfill_status.json`。测试覆盖访问控制、真实验收样本、CSV、日期边界、半年窗口连续性、进程锁与原始值转换。GitHub Actions在Linux上执行数据库恢复及测试。

上传前已在Windows上验证全新目录从压缩包还原数据库、SHA256、SQLite完整性及687,951条行数，并使用Waitress独立启动查询。Docker配置随包提供，本机未安装Docker，未在本机执行容器构建。
