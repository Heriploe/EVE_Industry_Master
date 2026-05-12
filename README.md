# EVE Industry Tools

## 目录结构

```
eve/
├── data/                        静态游戏数据（随 patch 更新，只读）
│   ├── blueprints.yaml          蓝图配方（全量，4 MB）
│   ├── types.json               物品类型定义 / T2.json / ships.json
│   └── ...
│
├── resources/                   动态资源（API 同步 / 市场抓取后替换）
│   ├── corp/        blueprints.json · materials.json · industry_jobs.json
│   ├── character/   blueprints.json · materials.json
│   ├── market/      price_all.json · price_materials.json · jita_prices.json
│   └── auth/        token_cache.json
│
├── utilities/                   可复用库（各 app 共同依赖）
│   ├── io/          loaders.py  (load_json / save_json / load_yaml / load_csv_tsv)
│   ├── data/        name_mapping.py · config_utils.py
│   ├── esi/         esi_auth.py  (ESI OAuth 2.0)
│   ├── market/      order_utils.py · price_history.py · price_by_preset.py
│   ├── blueprint/   blueprint_utils.py · expand.py
│   └── industry/    cost.py · t2_costs.py
│
└── apps/
    ├── industry_planner/   ★  BOM + 模拟调度 + 图表
    │   config.json · final_products.csv
    │   planner.py · sim_engine.py · visualizer.py
    │
    ├── asset_fetcher/         ESI 资产同步
    │   config.json · fetch_assets.py · fetch_blueprints_by_container.py
    │
    ├── market_fetcher/        市场价格抓取
    │   config.json · fetch_price_all.py · fetch_price_by_preset.py · fetch_structure_orders.py
    │
    ├── market_analyzer/       市场分析与出售策略
    │   config.json · get_item_price.py · split_item_to_sell.py
    │   split_direct_sell.py · split_scrap_metal.py · filter_execution_list.py
    │
    ├── blueprint_manager/     蓝图管理与缺口分析
    │   config.json · export_lacked.py · expand_by_container.py · expand_final_products.py
    │
    └── production_calc/       整数规划 + 矿石优化
        config.json · calculator.py · restore_ore.py
```

## 快速开始

```bash
# 同步资产（需 ESI token）
python eve/apps/asset_fetcher/fetch_assets.py

# 抓取市场价格
python eve/apps/market_fetcher/fetch_price_all.py

# 生产规划
cd eve/apps/industry_planner
python planner.py              # 默认参数
python planner.py --days 2 --me 8 --slots-mfg 12
python visualizer.py           # → outputs/industry_planner/charts/

# 分析出售策略
cd eve/apps/market_analyzer
python split_item_to_sell.py item_list.csv
```

## 配置说明

每个应用的 `config.json` 自包含所有路径和参数，路径相对于 `eve/` 根目录。
命令行参数可覆盖 config 值：

```bash
python planner.py --config /other/config.json --root /other/eve/
```

| 目录 | 内容 | 更新频率 |
|------|------|----------|
| `data/` | 游戏配方/类型 | 游戏 patch |
| `resources/corp/` | 军团库存 | API 同步 |
| `resources/market/` | 市场价格 | 每日 |
| `apps/*/config.json` | 应用参数 | 手动 |
| `outputs/` | 运行结果 | 每次运行 |
