# Healthcare-bot 🏥

An intelligent healthcare chatbot powered by LLMs, LangChain, and RAG (Retrieval-Augmented Generation) technology. This application provides conversational AI capabilities for healthcare-related inquiries using a local Ollama model and a vector database for context-aware responses.

## 📋 Table of Contents

- [Features](#features)
- [Project Structure](#project-structure)
- [Technology Stack](#technology-stack)
- [Quick Setup](#quick-setup)
- [Configuration](#configuration)
- [Usage](#usage)
- [Development](#development)

## ✨ Features

- **AI-Powered Conversations**: Leverages state-of-the-art LLMs (Llama 3.1) via Ollama for intelligent healthcare responses
- **Retrieval-Augmented Generation (RAG)**: Uses ChromaDB for vector storage and semantic search to provide contextually relevant answers
- **FastAPI Backend**: High-performance async web API for serving the chatbot
- **Interactive Dashboard**: Web-based UI for interacting with the healthcare bot
- **Database Integration**: SQLite with SQLAlchemy ORM for data persistence
- **LangGraph Agent**: Agentic workflow orchestration for complex healthcare queries
- **Logging & Monitoring**: Comprehensive logging with Loguru
- **Testing Suite**: Pytest integration for unit and async testing
- **Environment Configuration**: Flexible environment-based configuration management

## 📁 Project Structure

```
Healthcare-bot/
├── app/                          # Main application package
│   ├── agents/                   # LangGraph agents for workflow orchestration
│   ├── api/                      # FastAPI route handlers and endpoints
│   ├── core/                     # Core business logic
│   ├── db/                       # Database models and utilities
│   ├── rag/                      # RAG pipeline implementation
│   ├── tools/                    # Custom tools for agents
│   ├── utils/                    # Utility functions and helpers
│   ├── main.py                   # FastAPI application entry point
│   └── config.py                 # Configuration management
├── dashboard/                    # Frontend web interface
│   └── index.html                # Interactive UI dashboard
├── data/                         # Data storage directory
│   ├── healthcare.db             # SQLite database
│   └── chroma/                   # ChromaDB vector store
├── scripts/                      # Utility scripts
├── tests/                        # Test suite
├── requirements.txt              # Python dependencies
├── .env.example                  # Environment variables template
└── .env                          # Environment configuration (local)
```

## 🛠 Technology Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| **Framework** | FastAPI | 0.111.1 |
| **Server** | Uvicorn | 0.30.1 |
| **LLM Orchestration** | LangChain | 0.2.6 |
| **Agentic Workflow** | LangGraph | 0.1.19 |
| **Vector Database** | ChromaDB | 0.5.3 |
| **Database** | SQLite + SQLAlchemy | 2.0.31 |
| **Language** | Python | 3.x |
| **Async Support** | AsyncIO | Built-in |
| **Testing** | Pytest | 8.2.2 |
| **Logging** | Loguru | 0.7.2 |
| **Data Validation** | Pydantic | 2.7.4 |

## 🚀 Quick Setup

### Prerequisites

- Python 3.8 or higher
- [Ollama](https://ollama.ai/) installed and running locally
- pip package manager

### Installation Steps

1. **Clone the repository**
   ```bash
   git clone https://github.com/Eshaan-AIMILL/Healthcare-bot.git
   cd Healthcare-bot
   ```

2. **Create and activate a virtual environment** (recommended)
   ```bash
   # On macOS/Linux
   python3 -m venv venv
   source venv/bin/activate
   
   # On Windows
   python -m venv venv
   venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**
   ```bash
   # Copy the example environment file
   cp .env.example .env
   ```

5. **Ensure Ollama is running**
   ```bash
   # Start Ollama service (if not already running)
   ollama serve
   
   # In another terminal, pull the required model
   ollama pull llama3.1:8b-instruct-q8_0
   ```

6. **Run the application**
   ```bash
   # Start the FastAPI server
   python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

7. **Access the application**
   - API Documentation: http://localhost:8000/docs
   - Dashboard: http://localhost:8000/dashboard (if configured)

## ⚙️ Configuration

Configure the application by editing the `.env` file:

```env
# Ollama LLM Configuration
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b-instruct-q8_0

# Database Configuration
DATABASE_URL=sqlite+aiosqlite:///./data/healthcare.db
CHROMA_PERSIST_DIR=./data/chroma

# Application Settings
LOG_LEVEL=INFO
SEED_ROWS_PER_TABLE=2000

# RAG Configuration
RAG_CHUNK_SIZE=800
RAG_CHUNK_OVERLAP=120
```

### Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `OLLAMA_BASE_URL` | http://localhost:11434 | Ollama service endpoint |
| `OLLAMA_MODEL` | llama3.1:8b-instruct-q8_0 | Language model identifier |
| `DATABASE_URL` | sqlite+aiosqlite:///./data/healthcare.db | Database connection string |
| `CHROMA_PERSIST_DIR` | ./data/chroma | Vector database storage location |
| `LOG_LEVEL` | INFO | Logging verbosity level |
| `SEED_ROWS_PER_TABLE` | 2000 | Sample data rows for seeding |
| `RAG_CHUNK_SIZE` | 800 | Document chunk size for RAG |
| `RAG_CHUNK_OVERLAP` | 120 | Overlap between chunks for context |

## 💻 Usage

### API Endpoints

Access the interactive API documentation at `http://localhost:8000/docs` for all available endpoints.

### Running Tests

```bash
# Run all tests
pytest

# Run tests with async support
pytest -v --asyncio-mode=auto

# Run tests with coverage
pytest --cov=app tests/
```

### Development Server

The application includes hot-reload for development:

```bash
python -m uvicorn app.main:app --reload --port 8000
```

## 📦 Dependencies Overview

- **FastAPI & Uvicorn**: Web framework and ASGI server
- **LangChain & LangGraph**: LLM orchestration and agent workflows
- **ChromaDB**: Vector storage for RAG
- **SQLAlchemy**: Database ORM
- **Pydantic**: Data validation and settings management
- **Ollama Integration**: Local LLM inference
- **Pytest**: Testing framework

## 🔍 Key Modules

- **agents/**: Contains LangGraph agent definitions for handling complex healthcare workflows
- **api/**: FastAPI route definitions and request/response handlers
- **rag/**: RAG pipeline including embeddings, retrieval, and context generation
- **db/**: Database models, migrations, and query utilities
- **tools/**: Custom tools exposed to agents for healthcare operations
- **utils/**: Shared utilities including loggers, validators, and helpers

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📝 License

This project is open source and available under the MIT License.
