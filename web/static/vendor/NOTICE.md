# 本地文档排版组件

固定版本文件通过 npm 的 unpkg 分发端下载；运行时从本项目静态目录加载，不向 CDN 发送文档。

- MathJax 3.2.2：`mathjax/tex-svg-full.js`，Apache-2.0，许可证见同目录 LICENSE。
- marked 12.0.2：`marked/marked.min.js`，MIT，许可证见同目录 LICENSE.md。
- DOMPurify 3.2.7：`dompurify/purify.min.js`，Apache-2.0 或 MPL-2.0，许可证见同目录 LICENSE。

Markdown 经白名单净化后显示；TeX 仅启用 base、ams、newcommand，不启用任意 URL 或扩展动态加载。公式 SVG 内嵌字形路径，下载 HTML 不依赖数学字体或脚本。
