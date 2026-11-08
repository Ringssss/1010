# C2必要启动检查

24项通过。源代码与157项部署资产保持、追加源码0；GPU4/NUMA4/BDF85、预算、门槛、模型与分析冻结引用保持。仅新session、mode文件、监督输出与dump路径变化，顺序固定V2→V1。

C1独审与冻结引用精确匹配，两份事前预测已在C2前保存；stage的原容器、端口及源码检查通过。本检查继承C1已验收CPU/隔离CLI实现，不重复整个测试集。新runtime物理门仍由执行阶段核对。

审查器首次试运行误用字段名 `predeclared_ref`，在未产生输出目录时触发KeyError；读取实际 `analysis_predeclared_ref` 后通过，仅修改review代码。
