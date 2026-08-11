# Python 依赖锁

`bootstrap.lock` 固定安全的 pip，`base.lock` 固定 Python 3.11 的生产依赖，`dev.lock` 固定应用、测试与质量工具依赖。锁生成环境与开发/CI 环境必须分开，因为 pip-tools 7.6.0 支持 pip 26.0 系列，但不兼容 pip 26.2 的内部 API。

更新直接依赖后，使用一次性的锁生成环境：

```powershell
python -m venv .lock-venv
.\.lock-venv\Scripts\python.exe -m pip install pip==26.0.1 pip-tools==7.6.0
.\.lock-venv\Scripts\pip-compile.exe --allow-unsafe --generate-hashes --resolver=backtracking --strip-extras --output-file backend\requirements\bootstrap.lock backend\requirements\bootstrap.in
.\.lock-venv\Scripts\pip-compile.exe --generate-hashes --resolver=backtracking --strip-extras --output-file backend\requirements\base.lock backend\requirements\base.in
.\.lock-venv\Scripts\pip-compile.exe --generate-hashes --resolver=backtracking --strip-extras --output-file backend\requirements\dev.lock backend\requirements\dev.in
```

开发和 CI 安装时必须先升级到已审计 pip，再验证完整依赖哈希：

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r backend\requirements\bootstrap.lock
.\.venv\Scripts\python.exe -m pip install --require-hashes -r backend\requirements\dev.lock
```

生产镜像只安装 `bootstrap.lock` 和 `base.lock`，不安装测试与质量工具。
