# CLAUDE.md — Jornal-DE-JP

## O que é
Bot de estudo de japonês (N3→N2) e alemão (A2→B1) no Telegram, com Gemini (gratuito) e gTTS.

| Fluxo | Arquivo | Horários (Brasília) | O que faz |
|---|---|---|---|
| `jornal` | `send.py` (`MODO=jornal`) | 06:30 🇯🇵, 08:00 🇩🇪, 12:30 🇯🇵, 17:30 🇩🇪, 19:30 🇯🇵, 21:00 🇩🇪 (+ resumo no domingo) | Texto do slot + áudios (normal e shadowing) + quiz |
| `respostas` | `send.py` (`MODO=respostas`) | de hora em hora, 07:15–23:15 | Lê o chat: correção de frase, avaliação de voz, comandos (soube, não soube, nivel, resumo…) |
| `cultura` | `cultura/cultura.py` | 20:30 🎼 obra musical | A pintura das 10h foi desligada (agora sai pelo `diario-cultural`) |

- Estado: `data/state.json` e `cultura/state.json` (gravados pelo workflow). Prompts: `prompts/jp.txt`, `prompts/de.txt`.
- Se mudar um horário do `jornal.yml`, ajuste também `SLOTS`/`CRON_SLOT` no `send.py`.
- Os 3 workflows compartilham `concurrency: jornal-state` (não rodam juntos).

## Segredos e variáveis (só os nomes)
- Secrets: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`, `PINTURA_TELEGRAM_TOKEN` (pintura, desligada)
- Variables: `GEMINI_MODEL`, `TELEGRAM_THREAD_ID` (tópico 🇯🇵🇩🇪 Idiomas), `CULTURA_THREAD_ID` (música → 🎨 Diário cultural), `TELEGRAM_AVISOS_THREAD_ID` (⚠️ Avisos)

## Como testar
- Sintaxe: `python -m py_compile send.py cultura/cultura.py cultura/musicas.py`
- Ensaio: Actions → `jornal` → Run workflow (campo `slot`, ex. `12:30`; `sem_audio` para ir mais rápido)
- Actions → `respostas` → Run workflow; Actions → `cultura` → Run workflow (`tipo: musica`)

## Regras do Pedro
- Pedro não tem computador: tudo roda no GitHub Actions e se configura pelo navegador do celular (Android).
- Não quebre o que funciona. Antes de mudar, leia o código e os workflows e resuma em 5 linhas o que existe.
- Teste antes (pytest, execução de ensaio ou `workflow_dispatch`); depois commit e push direto na `main`. Push bloqueado → abra PR e faça o merge. Teste falhou e não dá para corrigir → desfaça e explique.
- Mensagens dos bots em português.
- Segredos só via `secrets`/`vars` do GitHub. Nunca chave no código nem no log; nunca peça token colado na conversa.
- Dependeu de decisão do Pedro → pare e pergunte em uma frase, com opções.
- O conteúdo de estudo continua em japonês e alemão; o resto em português.
- **Privacidade:** o bot é admin do grupo e recebe tudo. Só processa mensagens do próprio tópico (`do_meu_topico` em `send.py`); as de outros tópicos (📸 Família, 📝 Rascunhos…) são descartadas sem salvar, logar ou mandar ao Gemini.
- Erros vão para ⚠️ Avisos via `avisos.py` (mesma falha 2x seguidas, 1 aviso/dia, ignora rede/429/5xx).
- **Custo:** não adicione chamadas à API do Claude. Use o Gemini que já está aqui ou nenhuma IA; se algo realmente precisar do Claude, pergunte antes.

## Como trabalhar
- **Pense antes de codar:** diga as suposições em voz alta; se o pedido aceita mais de uma leitura, pergunte em vez de escolher calado.
- **Simplicidade primeiro:** escreva o menor código que resolve. Nada de opção, camada ou recurso que não foi pedido.
- **Mudanças cirúrgicas:** toque só nas linhas necessárias. Não reformate, não renomeie, não "melhore" o código vizinho.
- **Objetivo verificável:** antes de começar, diga como saber que deu certo (teste ou execução de ensaio). Só termine quando passar.

