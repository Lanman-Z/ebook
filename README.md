# EBook Demo App

一个基于 FastAPI 的本地电子书测试应用，支持：

- 上传 `PDF` 和 `EPUB`
- 查看所有已上传书籍
- 在浏览器中阅读解析后的文本内容
- 选中一句话或一段文字后发布公开书评
- 局域网内其他设备通过你的 IP 访问同一服务

## 目录

- `app.py`：FastAPI 后端入口
- `templates/index.html`：测试前端页面
- `static/styles.css`：页面样式
- `data/`：运行后自动生成，包含 SQLite 数据库和上传文件

## 安装依赖

```powershell
pip install -r requirements.txt
```

如果你的电脑同时装了多个 Python，也可以用：

```powershell
py -m pip install -r requirements.txt
```

## 启动服务

```powershell
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

启动后：

- 本机访问：`http://127.0.0.1:8000`
- 同一局域网访问：`http://你的局域网IP:8000`

例如你的电脑 IP 是 `192.168.1.8`，其他设备就可以打开：

```text
http://192.168.1.8:8000
```

## 注意

- 这个版本是测试用原型，前端是写死的单页页面。
- EPUB 会提取其中的 HTML/XHTML 文字内容显示，不是原版排版阅读器。
- PDF/EPUB 如果本身是扫描图片而不是文字，可能无法正常解析。
