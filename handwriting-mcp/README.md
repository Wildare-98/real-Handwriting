# Handwriting MCP Server

**MCP Server** 封装的手写体渲染工具。接收文本，输出 A4 手写风格的 PNG 图片与 PDF。

## 功能

- 将纯文本渲染为带有随机抖动、基线漂移、斜行、涂改痕迹的真实手写效果
- 支持白纸 / 素纸 / 横线 / 网格四种纸张背景
- 内置「青叶手写体」字体（约 3MB），开箱即用
- 提供 MCP 工具接口，可在 WorkBuddy / Claude Desktop / 任何 MCP 客户端中使用

## MCP 工具

| 工具 | 说明 |
|---|---|
| `handwriting_render` | 渲染文本为手写 PNG + PDF，可调 30+ 参数 |
| `handwriting_list_presets` | 列出可用的渲染预设 |

## 安装

### 方式一：pip 安装（推荐本地使用）

```bash
pip install git+https://github.com/xuyiyang/handwriting-mcp.git
```

### 方式二：克隆运行

```bash
git clone https://github.com/xuyiyang/handwriting-mcp.git
cd handwriting-mcp
pip install -r requirements.txt
```

## 本地使用（WorkBuddy / Claude Desktop）

在 MCP 配置中添加：

```json
{
  "mcpServers": {
    "handwriting": {
      "command": "python",
      "args": ["-m", "handwriting_mcp.server"]
    }
  }
}
```

## 部署到远程平台

### 方案一：Smithery（最简单的 MCP 托管）

1. 将项目推送至 GitHub
2. 在 [smithery.ai](https://smithery.ai) 注册并连接仓库
3. Smithery 自动构建并生成 MCP 连接 URL

### 方案二：Docker + 任意云平台

```bash
# 构建镜像
docker build -t handwriting-mcp .

# 本地运行（SSE 模式）
docker run -p 8080:8080 handwriting-mcp

# 推送到任意容器平台（Railway / Fly.io / Koyeb / 阿里云等）
docker tag handwriting-mcp registry.example.com/handwriting-mcp
docker push registry.example.com/handwriting-mcp
```

### 方案三：CloudStudio

WorkBuddy 内置 CloudStudio 部署支持，可直接将构建产物部署为静态/动态站点。

### 方案四：Railway / Fly.io / Koyeb

这类平台直接支持 Docker 部署，只需连接 GitHub 仓库，选择 Dockerfile 构建即可。

## 上传到 GitHub

```bash
cd handwriting-mcp
git init
git add .
git commit -m "Initial commit: handwriting MCP server"
git remote add origin https://github.com/YOUR_USERNAME/handwriting-mcp.git
git branch -M main
git push -u origin main
```

## 使用示例

在 WorkBuddy 中安装此 MCP 后，直接对话即可触发渲染：

> "帮我把这段文本渲染成手写体：\n患者，男，35岁。主诉：头痛3天……"

## 项目结构

```
handwriting-mcp/
├── handwriting_mcp/
│   ├── __init__.py
│   ├── renderer.py        # 核心渲染引擎
│   ├── server.py          # MCP 服务端
│   └── fonts/
│       └── 青叶手写体.ttf
├── presets/
│   └── default.json       # 默认预设
├── pyproject.toml
├── requirements.txt
├── Dockerfile
├── smithery.yaml
└── README.md
```

## 参数说明

所有 `handwriting_render` 参数（除 `text` 外均有默认值）：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `text` | (必填) | 要渲染的文本内容 |
| `paper` | white | 纸张：white / plain / lined / grid |
| `ink` | #000000 | 墨水颜色 #RRGGBB |
| `font_size` | 90 | 基础字号（像素） |
| `line_height` | 100 | 行高（像素） |
| `mistake_chance` | 0.01 | 每字涂改概率 (0–1) |
| `seed` | None | 固定随机种子（可重现） |

完整参数列表见 `handwriting_render` 工具定义。

## 许可

MIT License
