# ApiMonitor 🚀

ApiMonitor é um sistema de monitoramento de endpoints de alta performance, desenhado para escalar. Ele foi construído com uma arquitetura moderna e assíncrona para garantir precisão, baixo consumo de recursos e alta concorrência.

## 🛠️ Tecnologias Utilizadas
- **Backend:** FastAPI, Python (Assíncrono)
- **Banco de Dados:** PostgreSQL (via asyncpg e SQLAlchemy 2.0)
- **Filas & Tarefas:** Redis + ARQ (Async Redis Queue)
- **Agendamento:** APScheduler (com Distributed Lock via Redis)
- **Frontend:** Vanilla JS, CSS Glassmorphism & Chart.js para gráficos de latência.

## 📌 Arquitetura
Para garantir que o sistema não trave o evento principal do FastAPI com pings lentos, a arquitetura foi dividida em:
1. **API (FastAPI):** Serve o painel, gerencia o banco de dados, cria endpoints, usuários e abre a conexão WebSocket para atualizações em tempo real.
2. **Scheduler:** Apenas UMA instância obtém o "lock" do Redis e enfileira os pings dos endpoints no tempo correto para evitar disparos duplicados.
3. **Worker (ARQ):** Um ou mais processos paralelos consomem a fila no Redis, realizam as requisições HTTP (pings) e salvam no PostgreSQL, alertando via Discord se algo cair.

---

## 🚀 Como instalar e rodar o projeto

Certifique-se de ter o **Python (3.9+)** e o **Docker** instalados na sua máquina.

### 1. Preparar Variáveis de Ambiente
Copie o arquivo de exemplo e crie o seu `.env` na raiz do projeto:
```bash
cp .env.example .env
```
*(Edite o `.env` se precisar trocar as senhas ou configurar o alerta do Discord).*

### 2. Iniciar o Docker e os Bancos de Dados
Antes de rodar os containers, o serviço do Docker precisa estar ativo. Se estiver no Windows usando o Docker Desktop, você pode iniciá-lo abrindo o aplicativo pelo menu ou rodando este comando no PowerShell:
```powershell
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
```
*(Aguarde o ícone do Docker na barra de tarefas indicar que ele está rodando).*

Na raiz do projeto, suba os containers do PostgreSQL e do Redis em segundo plano:
```bash
docker-compose up -d
```
*(Para parar e destruir os containers no futuro, use: `docker-compose down`)*

### 3. Criar Ambiente Virtual e Instalar Dependências
É fortemente recomendado usar um ambiente virtual (venv) para isolar as bibliotecas:

**No Windows:**
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**No Linux/Mac:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## ⚡ Comandos para Iniciar o Sistema

Para o ApiMonitor funcionar completamente, você **precisa rodar a API e o Worker simultaneamente** em janelas de terminal separadas (garanta que o `venv` esteja ativado em ambas).

### Terminal 1: Iniciando a API (FastAPI)
Este comando inicia o servidor web e o painel.
```bash
cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```
*(Dica para desenvolvimento: Adicione `--reload` no final do comando para o servidor reiniciar sozinho sempre que você salvar um arquivo).*

### Terminal 2: Iniciando o Worker (ARQ)
Este comando inicia o trabalhador invisível que processa os pings nos sites e manda alertas.
```bash
cd backend
python -m arq worker.WorkerSettings
```

---

## 💻 Acessando o Dashboard

Após iniciar os dois terminais, abra no seu navegador: 
👉 `http://localhost:8000/`

**Login Padrão:**
- **Usuário:** admin
- **Senha:** admin

*(O usuário padrão é criado automaticamente na primeira vez que o banco de dados é iniciado. Na aba "Usuários" você pode adicionar novas contas ou remover antigas).*
