# Portal do Colaborador — Grupo Global / Globaltech Brasil LTDA

Os funcionários são atendidos pelo **WhatsApp** (Assistente do Colaborador): contracheques, termo de VT, registro
de empregado, pedidos e dúvidas. Eles não têm login: o assistente reconhece cada um pelo celular do cadastro.
O site é o **Painel do RH**; o endereço público (`/`) só explica que o atendimento é pelo WhatsApp.

## Rodar no computador (Windows / PowerShell)

Primeira vez:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # ativa o ambiente (aparece "(.venv)" no início da linha)
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate               # cria as tabelas do banco
python manage.py carregar_dados_iniciais
python manage.py createsuperuser       # cria o seu login de administrador (RH)
```

Nas próximas vezes:

```powershell
.\.venv\Scripts\Activate.ps1
python manage.py runserver 8010
```

Abra http://127.0.0.1:8010/admin/ no navegador.

Rodar os testes automáticos: `python manage.py test`

Compartilhar pela internet (link temporário): `powershell -ExecutionPolicy Bypass -File .\scripts\compartilhar_ngrok.ps1`

## PC como servidor com link fixo (Cloudflare Tunnel) — solução atual

Custo: só o domínio (~R$ 40/ano no registro.br). A Cloudflare é gratuita e não precisa abrir portas no roteador.

1. **Domínio**: registre em registro.br.
2. **Cloudflare** (grátis): *Add a domain* > plano Free > copie os 2 *nameservers* e cole no registro.br
   (Domínio > DNS > Alterar servidores DNS). Aguarde o domínio ficar **Active**.
3. **Túnel**: Cloudflare > **Zero Trust** > **Networks** > **Tunnels** > *Create a tunnel* > **Cloudflared** >
   nome `portal` > copie o **token** (o texto longo depois de `service install`).
   Em **Public Hostname**: subdomínio `portal`, seu domínio, serviço **HTTP** `localhost:8020`.
4. **No PC servidor**, PowerShell **como administrador**, na pasta do projeto:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\instalar_servidor.ps1 -Dominio portal.SEU-DOMINIO.com.br -TokenCloudflare "TOKEN"
   ```
   O portal passa a ligar sozinho com o Windows, o backup roda todo dia às 02:00 (`dados_rh\backups`, guarda 7)
   e o PC não entra mais em espera na tomada. Registros em `logs\portal.log`.
   Para desfazer: `scripts\desinstalar_servidor.ps1` (os dados não são apagados).

Cuidados: PC ligado na tomada (de preferência com nobreak) e internet por cabo.

**Backup fora do PC (Google Drive da empresa)** — configuração em uso:
Google Drive para computador (conta da empresa) > engrenagem > Preferências > **Meu laptop** > *Adicionar pasta* >
`funcionarios\dados_rh\backups` > marcar **Sincronizar com o Google Drive** > Salvar. Os backups diários sobem sozinhos e
aparecem em drive.google.com > *Computadores* (o Drive também remove os antigos que o sistema apaga; ficam 7).
O Windows precisa ficar com um usuário conectado (tela bloqueada pode). Não compartilhe essa pasta.

## Banco de dados no Neon (PostgreSQL na nuvem)

Os PDFs continuam no disco do servidor (`arquivos_privados/`); só o banco vai para o Neon.

1. Em neon.tech: crie o projeto na região mais próxima (de preferência **São Paulo**) e copie a
   *connection string* (conexão direta, com `sslmode=require`).
2. Cole no `.env`: `DATABASE_URL=postgresql://...` (a senha do banco fica só no `.env`).
3. Conferir a compatibilidade (cria e apaga um banco de teste no Neon):
   `$env:TESTAR_NO_POSTGRES="True"; .\.venv\Scripts\python.exe manage.py test`
4. Levar os dados (o backup deve ser gerado ANTES de trocar o `.env`, a partir do SQLite):
   `python manage.py migrate` e `python manage.py importar_backup dados_rh\backups\ARQUIVO.zip --sem-arquivos`.

As sessões de login ficam em arquivos no servidor (`sessoes/`), não no banco, para não depender da internet a cada clique.

## Publicar na VPS da Hostinger com Coolify (servidor 24h)

O `Dockerfile` monta o portal; ao ligar, o `docker-entrypoint.sh` atualiza o banco, confere a segurança
(`verificar_publicacao`) e sobe o servidor. As variáveis prontas ficam em `dados_rh/coolify_variaveis.txt`.

1. **Domínio**: no painel de DNS do domínio, crie um registro **A** `portal` apontando para o IP da VPS.
2. **Coolify** (`http://IP-DA-VPS:8000`):
   - **Sources** > conecte o GitHub (GitHub App) com acesso ao repositório `funcionarios-GG`.
   - **+ New** > **PostgreSQL** > Start. Copie o **Postgres URL (internal)**.
   - **+ New** > **Private Repository (GitHub App)** > `funcionarios-GG`, branch `main`, **Build Pack: Dockerfile**,
     **Ports Exposes: 8000**, **Domains: `https://portal.SEU-DOMINIO.com.br`**.
   - **Persistent Storage** > Add > **Destination Path: `/data`** (guarda os PDFs).
   - **Environment Variables**: cole o conteúdo de `dados_rh/coolify_variaveis.txt` (com a URL do Postgres e o domínio).
   - **Deploy**.
3. **Levar os dados** (uma vez): `python manage.py exportar_backup` no computador e envie o .zip em
   `https://portal.SEU-DOMINIO.com.br/restaurar-backup/`. Depois apague `RESTAURAR_BACKUP_SENHA`.

Cada `git push` para o GitHub pode atualizar o servidor sozinho (ligue **Auto Deploy** no Coolify).


## Estrutura de pastas

```
funcionarios/
├── portal/            configurações do Django, painel do RH (menu: admin_site.py) e página pública
├── contas/            funcionários, contratos, logins do RH, importação de planilha
├── contracheques/     envio do PDF do mês e separação por matrícula
├── documentos/        registro de empregado e termo de VT (PDF separado por CPF)
├── solicitacoes/      pedidos dos funcionários (ex.: termo de VT devolvido pelo WhatsApp)
├── orientacoes/       orientações e formulários
├── auditoria/         registro de quem fez o quê (LGPD)
├── whatsapp/          Assistente do Colaborador (atendimento, simulador, conexão)
├── gateway-whatsapp/  conexão do WhatsApp por QR code (Node.js)
├── templates/         telas do painel do RH e página pública (HTML)
├── static/            CSS (painel.css, publico.css) e ícones
├── scripts/           servidor no PC (instalar/desinstalar), backup diário, compartilhar link
├── exemplos/          planilha fictícia para testes
│
├── dados_rh/          ⚠ arquivos de trabalho do RH: PDFs originais, planilhas, backups (não vai para o GitHub)
├── arquivos_privados/ ⚠ cópias protegidas que o sistema entrega aos funcionários (não mexer à mão)
├── db.sqlite3         ⚠ banco de dados
└── sessoes/, staticfiles/   gerados automaticamente pelo sistema
```

As pastas marcadas com ⚠ têm dados pessoais e estão no `.gitignore`.

## Módulos

| Pasta           | O que guarda                                                         |
|-----------------|----------------------------------------------------------------------|
| `contas`        | Contratos/lotações, funcionários (com o celular do WhatsApp) e logins do RH |
| `contracheques` | Upload mensal (PDF único), contracheques individuais, páginas pendentes |
| `documentos`    | Documentos individuais; termo de VT e registro separados por CPF     |
| `orientacoes`   | Central de Orientações: categorias, orientações, anexos e versões    |
| `solicitacoes`  | Tipos de solicitação (modelos de termo), solicitações e histórico    |
| `auditoria`     | Log de auditoria (LGPD) — somente leitura                            |
| `portal`        | Configurações, painel do RH (`admin_site.py`), armazenamento privado, backup e envios em lote |
| `whatsapp`      | Assistente do Colaborador: conversas, atendimento humano, simulador, conexão |

## Acessos

- **Funcionário:** pelo WhatsApp do assistente, com o celular cadastrado no RH. Sem login, sem senha, sem CPF.
  O endereço público (`/`) mostra o botão "Conversar no WhatsApp" quando o número está em
  *Configuração > número do assistente (divulgado)*.
- **RH (painel):** http://127.0.0.1:8010/admin/ (ou o endereço definido em `ADMIN_URL` no `.env`).
  Menu lateral por tarefa; cada pessoa só vê o que a permissão dela deixa abrir. Os números laranja mostram o que
  espera o RH. Antes de abrir na internet, `python manage.py verificar_publicacao` recusa senha fraca/provisória.
- Bloqueio do RH: após as senhas erradas definidas em *Configuração* (padrão 5), o login trava por 15 minutos.
  Emergência: `python manage.py desbloquear_login NOME_DO_USUARIO`.

## Importar funcionários por planilha

Painel > Funcionários > botão **Importar planilha** (ou **Baixar modelo de planilha**).
O funcionário é identificado pela matrícula: se já existir, é atualizado; se não, é criado.
A coluna **Telefone** precisa ser o **celular** (com DDD): é por ele que o assistente reconhece a pessoa.
Use "Somente conferir" para ver o relatório de erros sem salvar nada.
Há uma planilha fictícia para testes em `exemplos/planilha_teste_funcionarios.xlsx`.

## Termo de VT e Registro de empregado (um PDF com todos)

Painel > **Enviar termos e registros** > escolha o tipo e envie o PDF (uma página por funcionário).
O sistema separa pelo **CPF** de cada página e guarda o de cada funcionário:
- **Termo de opção de VT** → o funcionário pede no WhatsApp, assina e devolve (foto ou PDF) dizendo se quer
  "utilizar" ou "cancelar". O pedido chega em Painel > **Solicitações** como *Pendente*.
- **Registro de empregado** → o funcionário pede no WhatsApp.

Um envio novo do mesmo tipo substitui o anterior. Páginas com CPF fora do cadastro (ou nome diferente)
ficam em "Pendências" no relatório do envio, para vincular à mão ou entregar depois de cadastrar.

## Painel de acompanhamento, Excel e aviso por e-mail

- **Acompanhamento** (menu lateral): quantos já conversaram com o assistente, quem ainda **não recebeu o
  contracheque do mês** (lista em Excel, com telefone), pedidos parados há mais de 3 dias, mensagens por dia e
  números por contrato. "Recebeu" = o assistente enviou pelo WhatsApp (vem da auditoria).
- **Solicitações > Exportar para Excel**: exporta o que estiver filtrado na tela (ex.: só concluídas), com a coluna
  VT = Utilizar/Cancelar, pronta para a folha. Também há a ação "Exportar as selecionadas".
- **Aviso por e-mail**: com `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` e `EMAILS_AVISO_RH` no `.env`,
  o RH recebe um e-mail a cada pedido novo (sem CPF). Teste: `python manage.py testar_email`.

Toda exportação para Excel fica registrada na auditoria.

## Assistente do Colaborador (WhatsApp)

Módulo `whatsapp`: o funcionário conversa no WhatsApp e recebe contracheques, termo de VT e registro, devolve o
termo assinado (vira solicitação com `origem = WhatsApp`), acompanha pedidos e tira dúvidas. Regras:
- identifica pelo **celular do cadastro** (campo Telefone; o sistema guarda a forma `55DDD9XXXXXXXX` e aceita sem o 9),
  **sem pedir CPF nem senha**; número repetido em dois cadastros não recebe nada;
- se o RH trocar o celular de alguém, os documentos e avisos passam a ir só para o número novo;
- desligado sem a opção "ainda pode pedir documentos no WhatsApp" não recebe nada;
- envia **PDF** ou **link de 24 h** (Configuração); tudo na auditoria;
- dúvidas: responde só com a **Central de orientações** publicada; sem resposta ou assunto sensível → fila do RH.

Painel (menu lateral > WhatsApp): *Atendimentos* (conversas, responder como RH, devolver ao assistente),
*Perguntas sem resposta*, *Simulador* (testar sem celular) e *Conexão (QR code)*.
Funcionários: coluna e filtro WhatsApp (sem celular / com celular / já conversou).
Permissão para atender: "Atender conversas do WhatsApp".

**Ligar o WhatsApp pelo QR code** (gateway próprio em `gateway-whatsapp/`, Node.js + Baileys; não precisa de domínio):
1. Instale o Node.js LTS e rode `powershell -ExecutionPolicy Bypass -File .\scripts\gateway_whatsapp.ps1`.
   Na primeira vez ele instala as dependências e grava `WHATSAPP_CANAL=gateway` e uma senha
   (`WHATSAPP_GATEWAY_TOKEN`) no `.env`. **Reinicie o portal** depois disso.
2. Painel > *WhatsApp > Conexão*: aparece o QR code. No celular do chip: WhatsApp > Dispositivos conectados >
   Conectar um dispositivo. A mesma tela mostra o número conectado e o botão **Desconectar**.
3. Ligue "atendimento pelo WhatsApp" em Configuração.
- O gateway só escuta em `127.0.0.1:3005` e as duas pontas conferem a senha. Ele manda as mensagens para
  `http://127.0.0.1:8020` (mude com `WHATSAPP_GATEWAY_PORTAL` no `.env` se o portal estiver em outra porta).
- `gateway-whatsapp/sessao/` guarda as chaves do WhatsApp conectado: **nunca** vai para o GitHub (está no `.gitignore`).
- No PC servidor, o `instalar_servidor.ps1` agenda a tarefa `PortalWhatsApp` (liga com o Windows) quando o `.env`
  tiver `WHATSAPP_CANAL=gateway`. Registros em `logs\gateway.log`.
- **Conexão não oficial:** o WhatsApp pode bloquear o número. Use um **chip secundário**, nunca o número oficial.
  Para parecer gente e não robô, o gateway marca as mensagens como lidas, mostra "digitando…", espera 1 a 3 s
  antes de cada resposta e manda no máximo WHATSAPP_GATEWAY_LIMITE_MINUTO mensagens por minuto (padrão 30), uma por vez.
  Cuidados: aquecer o chip por 1 a 2 semanas, começar com poucos funcionários, pedir que salvem o número e nunca
  usar o número para disparo em massa.

**Ligar o WhatsApp pela API oficial da Meta** (`WHATSAPP_CANAL=meta`; precisa do link fixo com HTTPS):
1. developers.facebook.com > criar app (tipo Empresa) > adicionar o produto **WhatsApp** com a conta Meta Business da
   empresa e um **número exclusivo** (não pode estar em uso no WhatsApp do celular).
2. No `.env`: `WHATSAPP_TOKEN` (token permanente de um *usuário do sistema*), `WHATSAPP_PHONE_NUMBER_ID`,
   `WHATSAPP_APP_SECRET` (Configurações do app > Básico) e uma senha sua em `WHATSAPP_VERIFY_TOKEN`.
3. Na Meta, em WhatsApp > Configuração > Webhook: URL `https://SEU-DOMINIO/whatsapp/webhook/`, token =
   `WHATSAPP_VERIFY_TOKEN`, e assine o campo **messages**.
4. Avisos fora da janela de 24 h (ex.: resposta do RH dias depois): crie um modelo de mensagem com uma variável
   `{{1}}` e coloque o nome em `WHATSAPP_MODELO_AVISO`.
5. Ligue "atendimento pelo WhatsApp" em Configuração.

**IA (opcional)**: com `ANTHROPIC_API_KEY` no `.env`, o Claude entende frases livres e responde dúvidas só com os textos
da Central (nunca recebe CPF, datas, telefones nem PDFs). Sem a chave, o assistente funciona com regras próprias.

## Proteções do sistema

- **Painel em endereço secreto** (`ADMIN_URL` no `.env`, criado pelos scripts); `/admin/` dá "não encontrado".
  `verificar_publicacao` não deixa abrir na internet com o painel em `/admin/`.
- **Senha do RH forte**: 10+ caracteres, misturando 3 tipos, sem nome da empresa/usuário. Senha nova fraca é recusada;
  quem entra com senha antiga fraca vê um aviso em todas as telas até trocar. Login trava após 5 erros (15 min) e
  muitos erros do mesmo IP são barrados.
- **Verificação em duas etapas** (Sistema > Verificação em 2 etapas): depois da senha, o código de 6 números do
  Google/Microsoft Authenticator (padrão TOTP, cada código vale uma vez). 8 códigos reserva de uso único para quem
  perder o celular; 5 códigos errados travam o login por 15 min. Emergência no PC servidor:
  `python manage.py desativar_duas_etapas USUARIO` (ou outro administrador desmarca em Usuários do RH).
- **Cabeçalhos de segurança** (`portal/seguranca.py`): CSP (só scripts/estilos do próprio portal, sem moldura em outro
  site), Permissions-Policy, HSTS e cookies seguros no modo público.
- **Gateway do WhatsApp**: só aceita chamadas de dentro do PC (o que vem pelo túnel é recusado mesmo com a senha).
- **Limite por número**: mais de 12 mensagens por minuto → o assistente avisa uma vez e para de responder no minuto.
- **LGPD**: conversas mais antigas que *Configuração > guardar as conversas por (dias)* (padrão 365) são apagadas toda
  noite (`limpar_whatsapp`, no backup diário). Pedidos, termos assinados e auditoria ficam.
- Dependências conferidas sem falhas conhecidas (`pip-audit` e `npm audit`).

## Segurança dos arquivos

Todos os arquivos enviados ficam em `arquivos_privados/`, com nomes aleatórios, e **não têm link público**.
O RH abre os arquivos pelo painel (exige login de administrador e fica registrado na auditoria).
O funcionário só baixa os próprios arquivos, por telas que conferem o dono (trocar o número no endereço dá
"não encontrado"). Ao apagar um registro, o arquivo dele também é apagado do disco.
