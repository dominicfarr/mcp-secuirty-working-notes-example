# mcp-secuirty-working-notes-example

simple reference on mcp security model through client policies and server elicitation

![MCP Security](https://github.com/dominicfarr/dominicfarr.github.io/blob/main/assets/mcp-security.svg)


### Setup
```bash
python3.11 -m venv mcp_security_env
source mcp_security_env/bin/activate
```

### Launch
```bash
pip install mcp==1.16.0 fastmcp==2.12.5 gradio==5.49.1 openai==2.6.1
```

### Execution

Start app, which wraps client base class, and instantiates the server in process for STDIO transport

```bash
python3 app.py server.py
```
