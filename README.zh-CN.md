# World Cup Agent Open

[English](README.md) | [简体中文](README.zh-CN.md)

这是一个面向 Codex 和 Claude Code 的开源世界杯赛事预测插件。

它会从你自己配置的数据源中获取赛前事实和赔率，构造成结构化特征向量，再输出带有概率、风险、覆盖度、预期进球、泊松比分、大小球和 BTTS 的比赛预测结果。

- 不提供前端
- 不提供托管后端
- 不提供消息推送
- 不执行投注

## 插件能做什么

- 用本地算法链路预测世界杯比赛
- 研究数据源和赔率数据源可独立配置
- 在攻防输入可用时，通过泊松比分模型推导推荐比分、大小球 2.5 和 BTTS
- 输出稳定 JSON，便于智能体直接解释
- 支持先跑离线无密钥 demo，再接入实时 API
- 仓库根目录直接包含 Codex 和 Claude Code 所需插件清单

开源版不保证盈利，也不能视为任何形式的投资或博彩建议。

## 算法说明

当前开源版的预测链路是一个本地、可复现的算法流程：

1. 获取近期战绩、球员状态、阵容、赛程、战意和赔率输入
2. 为目标比赛构造赛前特征向量
3. 对 8 个维度打分：`team_strength`、`recent_form`、`attack_defense_efficiency`、`schedule_fatigue`、`key_player_status`、`odds_movement`、`lineup_integrity`、`motivation_stage`
4. 用加权评分模型输出 1X2 概率
5. 从攻防效率推断主客队预期进球
6. 在输入足够时，用独立泊松比分模型推导比分分布、大小球 2.5 和 BTTS 概率
7. 当官方世界杯复盘样本足够时，再对基础概率做贝叶斯校准

对应实现位置：

- 加权赛前预测与路由: [src/scoring/pre_match_research_preview.py](src/scoring/pre_match_research_preview.py)
- 预期进球推断: [src/scoring/expected_goals.py](src/scoring/expected_goals.py)
- 泊松比分模型: [src/scoring/scoreline_model.py](src/scoring/scoreline_model.py)
- 贝叶斯校准: [src/scoring/bayesian_calibration.py](src/scoring/bayesian_calibration.py)
- 预测编排与持久化: [app/research_db/pre_match_research_scoring.py](app/research_db/pre_match_research_scoring.py)

## 五分钟启动

要求 Python 3.11 及以上。

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python scripts\run_demo.py
```

macOS/Linux:

```bash
source .venv/bin/activate
python -m pip install -e ".[dev]"
python scripts/run_demo.py
```

安装后的离线 demo 命令是：

```bash
world-cup-agent-demo
```

安装后的预测命令是：

```bash
world-cup-predict --local-date 2026-06-13
```

## 安装完成后怎么用

聊天窗口里直接输入一句话就够了：

```text
帮我预测明天的世界杯比赛
```

英文等价写法：

```text
Predict tomorrow's World Cup matches
```

如果只预测一场，直接补充日期、对阵双方或 fixture id 即可。

## 实时 API 环境变量配置

先复制配置模板：

```powershell
copy .env.example .env
```

```bash
cp .env.example .env
```

核心变量：

```dotenv
DEFAULT_RESEARCH_PROVIDER=auto
DEFAULT_ODDS_PROVIDER=auto
ENABLE_CRAWLER=true
SPORTRADAR_SOCCER_API_KEY=
THE_ODDS_API_KEY=
```

支持的 provider 取值：

- research: `auto`, `sportradar_soccer`, `crawler`, `skip`
- odds: `auto`, `the_odds_api`, `crawler`, `skip`

### API 配置示例

用 Sportradar 提供研究事实数据：

```dotenv
DEFAULT_RESEARCH_PROVIDER=sportradar_soccer
SPORTRADAR_SOCCER_API_KEY=<your-key>
```

用 The Odds API 提供赔率：

```dotenv
DEFAULT_ODDS_PROVIDER=the_odds_api
THE_ODDS_API_KEY=<your-key>
THE_ODDS_API_SPORT_KEY=soccer_fifa_world_cup
```

混合配置：

```dotenv
DEFAULT_RESEARCH_PROVIDER=sportradar_soccer
SPORTRADAR_SOCCER_API_KEY=<your-key>
DEFAULT_ODDS_PROVIDER=the_odds_api
THE_ODDS_API_KEY=<your-key>
```

### 官方注册链接

以下链接已于 2026 年 6 月 25 日核对：

- Sportradar Sports Data API 试用入口: [sportradar.com/media-tech/data-content/sports-data-api](https://sportradar.com/media-tech/data-content/sports-data-api/)
- Sportradar 开发者入门: [developer.sportradar.com/getting-started/docs/get-started](https://developer.sportradar.com/getting-started/docs/get-started)
- Sportradar 账号申请说明: [developer.sportradar.com/football/docs/football-ig-account-setup](https://developer.sportradar.com/football/docs/football-ig-account-setup)
- The Odds API 首页和免费套餐: [the-odds-api.com](https://the-odds-api.com/)
- The Odds API v4 文档: [the-odds-api.com/liveapi/guides/v4](https://the-odds-api.com/liveapi/guides/v4/)

说明：

- Sportradar 官方文档明确提供 free trial 注册路径。
- The Odds API 官网在 2026 年 6 月 25 日显示有免费 Starter 套餐，每月 500 credits。

## 可选 crawler 回退

Crawler 运行时不内置在仓库里。如果你要走自托管抓取链路，单独安装兼容 crawler 后再配置：

```dotenv
DEFAULT_RESEARCH_PROVIDER=crawler
DEFAULT_ODDS_PROVIDER=crawler
ENABLE_CRAWLER=true
SPORTS_STABLE_CRAWL_SCRIPTS_DIR=<path-to-crawler-scripts>
CRAWLER_PYTHON_PATH=<optional-python-with-crawl4ai>
```

脚本目录里至少要有：

- `whoscored_workflow.py` 用于研究事实
- `soccerway_odds.py` 用于赔率

## 稳定预测输出

开源版输出的是可直接被智能体解释的结构化结果，重点字段包括：

```json
{
  "schema_version": "world_cup_prediction.v1",
  "status": "ok | partial | failed",
  "predictions": [
    {
      "fixture_id": "fixture_wc2026_...",
      "match_time_beijing": "2026-06-13T08:00:00+08:00",
      "home_team": "Home",
      "away_team": "Away",
      "probabilities": {
        "home_win": 0.0,
        "draw": 0.0,
        "away_win": 0.0,
        "over_2_5": 0.0,
        "under_2_5": 0.0,
        "btts_yes": 0.0,
        "btts_no": 0.0
      },
      "expected_goals": {
        "home_expected_goals": 0.0,
        "away_expected_goals": 0.0
      },
      "scoreline_model": {
        "family": "independent_poisson"
      },
      "prediction_routing": {},
      "recommended_scores": ["1:1", "1:0", "2:1"],
      "risk": {
        "level": "low | medium | high",
        "confidence": 0
      },
      "coverage": {},
      "calibration": {},
      "gaps": []
    }
  ]
}
```

BTTS、推荐比分、大小球概率来自泊松比分路由；当攻防输入不足时，脚本会在 `gaps` 或 `prediction_routing` 中说明原因。资金分配和 risk/reward 只有脚本真实返回时才展示，不能由模型补写。

## 插件使用

仓库根目录就是插件根目录。

- Codex 插件清单: `.codex-plugin/plugin.json`
- Codex marketplace 清单: `.agents/plugins/marketplace.json`
- Claude Code 插件清单: `.claude-plugin/plugin.json`
- Claude Code marketplace 清单: `.claude-plugin/marketplace.json`
- 预测 skill: `skills/world-cup-prediction/`
- 辅助数据修复 skill: `skills/world-cup-research-backfill/`

安装与验证见 [PLUGIN_USAGE.md](PLUGIN_USAGE.md)。

## 更多文档

- [README.md](README.md): English version
- [ARCHITECTURE.md](ARCHITECTURE.md): provider 路由、数据流和算法边界
- [SECURITY.md](SECURITY.md): 密钥、crawler 信任边界和安全说明
- [PLUGIN_USAGE.md](PLUGIN_USAGE.md): Codex / Claude Code 安装与验证

## 验证

```bash
python -m compileall app src scripts tests
python -m pytest -q
python scripts/run_demo.py
python -m build
```

## License

MIT，见 [LICENSE](LICENSE)。
