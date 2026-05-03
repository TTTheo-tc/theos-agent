version: 1
vendors:
  openclaw:
    root: vendor/openclaw
    topics:
      channel_registry:
        goal: Compare channel registration and plugin boundaries.
        vendor_paths:
          - src/channels/registry.ts
          - src/channels/plugins/registry-loader.ts
        source_paths:
          - src/channels/registry.py
      tool_system:
        goal: Compare tool definition, registration, policy/permissions, and plugin extensibility patterns.
        vendor_paths:
          - src/agents/tool-catalog.ts
          - src/agents/tool-policy.ts
          - src/agents/tool-policy-pipeline.ts
          - src/agents/openclaw-tools.ts
          - src/plugins/tools.ts
        source_paths:
          - src/agent/tools/base.py
          - src/agent/tools/registry.py
          - src/agent/tool_sets.py
      memory_recall:
        goal: Compare long-term memory recall and storage boundaries.
        vendor_paths:
          - extensions/memory-lancedb/index.ts
        source_paths:
          - src/memory/structured.py
          - src/agent/tools/structured_memory.py
  daily_stock_analysis:
    root: vendor/daily_stock_analysis
    topics:
      analysis_pipeline:
        goal: Learn orchestration patterns that may improve stock tool integration.
        vendor_paths:
          - src/core/pipeline.py
        source_paths:
          - src/agent/tools/stock.py
