"""vtx.pluginlib —— 插件协议运行时（v2 §1.1 条目 8 / §2，设计权威 docs/ARCHITECTURE.md）。

纯 python3 标准库、**零 vtx 模块依赖**（上下文 c 经鸭子类型传入——pluginlib 被
infra/hygiene/cache_state/stages 依赖，反向依赖会成环）；模块内全部相对导入。

  manifest.py   app.json / plugin.json 加载与校验（§2.3 manifest schema v1；
                损坏 manifest 收集全部错误不崩）
  registry.py   发现（内置扫描 apps/ plugins/ + config external_*）与
                plugins/registry.json 静态注册表（键声明汇总 = 「未装插件报错
                文案」数据源，§2.1 不变量 3）
  hooks.py      钩子总线（缝 A prepare_source / 缝 B audio_events，§2.2）+
                三不变量聚合入口（contribute_keys / active_plugins_digest /
                get_cache_dirs）；P1-2 时点 plugins/ 无实体包，总线空转
  config.py     config 统一加载（§5.3：VTX_CONFIG > repo config.local.json；
                坏 JSON 警告+{}；已知路径键 ~ 展开；IMGEN_CONFIG 废弃公告）

三不变量（§2.1）的内核接线位置（P1-2 完成，空转零行为变化）：
  ① 缓存键贡献   hygiene._current_keys（ak 并集）/ gc_plan（cache 根白名单）
                 → hooks.contribute_keys / hooks.get_cache_dirs
  ② 回执指纹     cache_state.receipt_fingerprint 末尾追加 hooks.active_plugins_digest
  ③ 安装态三分支 stages.validate 插件键扫描 → registry.lookup_key / plugin_state
"""
