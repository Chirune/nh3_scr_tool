# GitHub 进度分享说明

这次提交聚焦**一个可阅读的进度页和能复现的软件源码**。从仓库首页进入[项目进度](PROJECT_STATUS_2026-10-01.md)，可以看到当前阶段、已验证的功能、真实论文小样结果与下一步。

仓库保留 `scrtool/`、`scripts/`、`browser_extension/`、`tests/`、`examples/` 和精简说明。以下材料仍留在本地：`evaluation/` 自动生成的测试输出、`output/` 科研运行结果、`release/` EXE、`build/` 和 `dist/`、真实论文 PDF、个人项目、密钥、专利计划，以及旧的详细进度/真实论文报告。历史上已跟踪的详细结果会从 Git 索引移除，本机文件不删除。

本地提交完成后，在项目目录运行 `git status` 查看待上传的提交，再运行 `git push origin main`。本次整理不会自动向 GitHub 推送。若以后需要分享 EXE，可单独发布经复核的交付包；不要把构建产物塞进源码提交。
