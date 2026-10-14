# LIMSData

独立的化验数据趋势查询系统。包含网页、Python接口、完整SQLite快照及可选SQL Server采集器，不依赖原MES项目。

## 数据

- 当前快照：**694,363条化验结果**；2026-10-13更新最近40天窗口，窗口内8,610条与源库核对一致。
- 取样日期：**2019-10-24至2026-10-12**。最初687,951条历史快照在2026-09-11完成源库总量核对。
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

对比不同样品或指标时，先查询第一组数据，点击“保留当前曲线”，再更改筛选并查询。保留曲线以虚线显示，新查询以实线显示；可以连续保留多组，逐条移除或一键清空。无数据或查询失败不会清除已保留曲线。

同单位曲线直接叠加，不同单位默认分图。勾选“变化幅度对比”可将每条曲线按各自最小值和最大值缩放到0–100%，同图比较变化走势；常数曲线显示在50%，悬停仍显示原值。该百分比不代表实际含量或涨跌幅。

保留项仅在当前页面会话有效，刷新或关闭页面后清除，不随URL保存；明细表和CSV只对应当前查询。最多保留40条曲线、60,000个时刻，图中总数最多40条。

“验收示例”选择2026-09-01至09-11、三排送、3P-1402、喹啉不溶物：真实源库仅一条，2026-09-07为 **1.53 % w/w**。选择2021-03-11至09-10可查看同一样品的18个历史测点。

原始结果和原始分析项保留；痕迹/少量/未检出沿用PBI的数值换算并标注。无效、不成线、文本和检出限不会伪造数值。大范围包含过多系列时，先选择装置、样品和分析项再查看图表。

## 异常样品页面

从趋势页点击“异常样品（近7天）”，或打开 `/lims/anomalies`。列表仅展示北京时间今天及此前6天内发生异常的系列，按取样日期判断，不以最后一次数据日期代替今天。可按装置、样品/分析项和异常类型筛选；数据过期或未判定项会明确提示。

排序先展示含质量指标超限的系列，再展示仅有SPC异常的系列。组内按近7天异常测点的最大绝对偏离σ倍数降序，同值依次按触发SPC规则种数、异常时刻数、最近异常时间排序。每张卡片显示最大偏离及对应测点的原因。历史不足或零波动基线无法计算σ倍数，排在同组可计算项之后。排序在分页前完成，筛选保留该顺序。

每张卡片默认最近10个取样时刻，支持自定义起止日期和“最近10点”恢复。异常测点为红色，悬停显示原始结果和触发规则，折叠明细保留文本结果。同取样时刻的原始数值取平均用于SPC和绘图，但任一原始结果超出质量指标均会标红，不会被均值掩盖。

质量指标来自《分析频度》ZBZ00001 F/0第38–51页，共91条，2026-08-01生效。配置位于 `config/quality_specs.json`，采用明确的装置、取样位置和分析项别名。定性QI按原始文本判定；文本、检出限和PBI换算值不用于SPC。Nm³和m³不自动视为同一体积基准；缺失单位、运行模式和未匹配标准列为未判定。附件没有独立产品质量指标表，因此成品执行SPC，仅明确取样位置对应的中间品条目适用质量限值，其余产品质量限值待补充。

SPC默认执行Western Electric四条规则和6点单调趋势、14点交替规则。每点使用此前最多30个数值时刻（至少20个）的均值作中心线，以平均移动极差/1.128估计σ。当前点不参与基线；连续规则标注完成该规则的末点。零波动基线的偏离单独提示。此为自动滚动基线，规则用于筛查复核；统计异常和指标超限分别列出。规则及公式参考 [NIST SPC规则](https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc32.htm) 和 [个体控制图](https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc322.htm)。图表日期调整不改变测点的历史判定。

## 已部署服务器更新

```bash
git pull
docker compose up -d --build
```

镜像更新保留现有数据卷。如果还需导入本次附带的已核对数据窗口，停止服务后运行以下命令；工具校验快照并保存完整备份，只合并最近40天的录入时间窗口，保护更早历史及窗口外数据，并拒绝覆盖更新的采集结果。

```bash
docker compose stop lims
docker compose run --rm lims python tools/install_snapshot.py
docker compose up -d
```

## 可选：继续从公司源库采集

查询不需要以下组件。只有需要继续采集时，才安装SQL Server ODBC驱动、`pip install -r requirements-collector.txt`，并确保运行机器能够连接公司数据库网络。

连接默认值在 `config/lims.json`；可用 `LIMS_SERVER`、`LIMS_PORT`、`LIMS_DATABASE`、`LIMS_DRIVER`、`LIMS_AUTH`覆盖。SQL登录使用环境变量 `LIMS_USERNAME` 和 `LIMS_PASSWORD`。Windows也可运行 `python tools/lims_credentials.py`交互式加密保存；凭据不应提交到Git。

```bash
python tools/lims_collector.py --days 140 --date-basis DATEENTER
python tools/lims_backfill.py
```

第二条命令沿用 `data/lims_backfill_status.json` 的固定回溯任务，跳过已完成窗口。已附带原十年回溯结果及后续窗口更新，因此再运行通常直接核对完成状态。后续近期数据使用第一条命令；更早历史不会被140天更新删除。异常页面判断本地数据，仍需定期从能访问公司网络的机器采集。

若确需网页按钮采集，设置 `LIMS_ENABLE_SYNC=true`。Docker默认没有安装ODBC驱动且关闭该功能。普通更新与历史回溯共用操作系统文件锁；网络故障不会覆盖已发布窗口。

## 验证

```bash
python bootstrap.py
python -m unittest discover -s tests -v
```

原历史回溯核对记录在 `seed/lims_backfill_acceptance.json`、`seed/lims_backfill_status.json`；本次增量采集核对在 `seed/lims_sync_status.json`。24项测试覆盖访问控制、真实验收样本、CSV、日期边界、半年窗口连续性、进程锁、SPC六条规则、定性QI、质量限值、单位隔离、近7天筛选、均值不掩盖原始超限和备份后快照合并。GitHub Actions在Linux上执行数据库恢复及测试。

已在Windows上验证压缩包SHA256、SQLite完整性及694,363条行数，并使用Waitress独立启动查询。Playwright使用真实近7天数据验证最近10点、红色异常标记、日期展开与恢复、范围调整后的判定一致性及指标超限筛选。Docker配置随包提供，本机未安装Docker，未在本机执行容器构建。
