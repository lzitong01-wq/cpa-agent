# -*- coding: utf-8 -*-
"""Agent 层：意图识别 + 编排 + 提示词 + 可配置大模型客户端。

对外入口是 app.agent.orchestrator.chat()，其余模块视为内部实现。
本层不直接写业务 SQL，全部通过 app.business.* 与 app.rag.service 调用，
保证业务层与 Agent 层可以各自替换。
"""
