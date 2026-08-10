# D 端网页版本留档规则

## 已保留的版本

- `visualization/prototype/week2_ui_prototype.html`：第二周的低保真交互原型；
- `visualization/prototype/archive/week3_integrated_runtime.html`：第三周的 A+B+C 真实联调初版；
- `visualization/prototype/archive/week4_integrated_runtime.html`：第四周的结果包导出、地图编辑器和策略页版本；
- `visualization/prototype/integrated_runtime.html`：当前可运行的最新版本。

## 后续约定

每进入新的一周，先将当前 `integrated_runtime.html` 复制为
`archive/weekN_integrated_runtime.html`，再在新的工作版本上继续修改。

这样周度成果可直接对比；本地 D 服务仍默认打开当前最新的
`integrated_runtime.html`，避免影响使用入口。

## 真实运行边界

“加载仓库真实示例（A+B+C）”必须通过本地 D 服务打开页面：

```text
python -m experiments.web_runtime_server
http://127.0.0.1:8765/visualization/prototype/integrated_runtime.html
```

直接双击 HTML 只能预览前端地图编辑器和演示功能。浏览器不能直接启动
Python 的 B 元胞自动机，因此页面会给出明确提示，而不会伪造真实运行结果。
