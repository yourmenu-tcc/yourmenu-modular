# Verificação dos artefatos nativos — 06/11/2026

**Atualização do mesmo dia:** implantação concluída na AWS e 13 verificações aprovadas; detalhes na seção “Implantação realizada”.

Revisão de código `07a6f95` (sem alterações nas fontes desde os builds). Os comandos de compilação e as saídas completas estão em `../2026-11-05/native-identity-admin.log` e `../2026-11-05/native-commerce.log`. Nenhum recurso AWS foi criado ou alterado.

## Build e ZIPs

| Módulo | Build Mandrel | `function.zip` | Conteúdo verificado |
|---|---|---:|---|
| `identity-admin` | `BUILD SUCCESS`, 3min53s | 33.789.075 bytes | ZIP íntegro; um `bootstrap` na raiz, modo `0755`; binário ELF64 x86-64 |
| `commerce` | `BUILD SUCCESS`, 3min48s | 33.796.600 bytes | ZIP íntegro; um `bootstrap` na raiz, modo `0755`; binário ELF64 x86-64 |

Os builds usaram Mandrel 25.0.5.0, `-march=x86-64-v2`, heap de 3.600 MB e duas threads. O SHA-256 de cada `bootstrap` extraído do ZIP é igual ao SHA-256 do executável `target/*-runner` correspondente. Cópias dos ZIPs estão em `artifacts/identity-admin-native.zip` e `artifacts/commerce-native.zip` para preservá-los caso `target` seja limpo; as cópias estão ignoradas pelo Git. Os hashes dos ZIPs e logs estão em `SHA256SUMS`; confira-os na raiz de `yourmenu-modular` com `sha256sum -c verification/2026-11-06/SHA256SUMS`.

## Execução funcional nativa

Foi executado `validate_native_local.py` a partir da raiz do projeto, com os executáveis nativos, um PostgreSQL 16 descartável, chaves RSA temporárias e um simulador local da API de runtime da Lambda. O esquema foi inicializado uma vez pela função `commerce` no banco descartável; em seguida, ambas as funções rodaram com a atualização de esquema desabilitada. O resultado foi **`passed`, 68 verificações aprovadas e 0 observações** em `native-validation/results.json`. Inclui login em `identity-admin` e uso do JWT em `commerce`, isolamento A/B, zonas de entrega, pedidos e resumo.

Os logs de inicialização registram `yourmenu-identity-admin ... native` e `yourmenu-commerce ... native`. A resposta 500 e a violação de `NOT NULL` em `identity-admin-none.log` pertencem a um caso intencional de rollback do teste. As mensagens `Socket closed` ocorreram quando o simulador foi desligado ao fim da validação. Esse ensaio não usa API Gateway, IAM, VPC ou RDS real; tampouco mede memória e duração da Lambda.

O verificador nativo foi derivado de `scripts/validate_local.py` em arquivo separado para esta execução: troca o lançamento dos JARs JVM pelos dois executáveis nativos e omite os dois `Maven verify` já executados na validação JVM de 03/11. Os arquivos de produção e o script original não foram modificados.

## Implantação AWS

Antes da implantação, **os dois artefatos nativos estavam prontos para funções Lambda `provided.al2023` de arquitetura x86-64, mas a infraestrutura ainda precisava ser configurada.** Os `target/sam.native.yaml` gerados criam APIs implícitas separadas e não definem o roteamento público único entre as duas funções. Também não incluem conexão de rede com o PostgreSQL, variáveis de banco e localização das chaves JWT, preparação do esquema nem permissões correspondentes. `MemorySize: 128` e `Timeout: 15` são exemplos gerados e ainda precisam de medição. O runtime nativo precisa de `DISABLE_SIGNAL_HANDLERS=true`.

Para declarar a aplicação pronta na AWS, é preciso preparar a infraestrutura, conferir o banco/esquema e os segredos, implantar as duas funções sob uma HTTP API e executar um teste real de login, zonas, isolamento A/B e pedidos pela URL pública. Nesta primeira sessão o CLI instalado via Snap recusou a execução dentro do sandbox. Uma [nova verificação da CLI](aws-cli-recheck.md) confirmou posteriormente o acesso de leitura fora do sandbox e identificou os recursos já existentes.

## Implantação realizada

O usuário autorizou a implantação. `python3 scripts/deploy_aws_modular.py --apply`, executado com a AWS CLI instalada no sistema fora do sandbox, criou em `us-east-1`:

- `yourmenu-modular-identity-admin` e `yourmenu-modular-commerce`: runtime `provided.al2023`, arquitetura `x86_64`, 1024 MB, 10 s, mesma VPC e grupo de segurança da Lambda serverless existente. O SHA-256 de cada código publicado na AWS coincide com o respectivo ZIP nativo arquivado em `artifacts/`; ambas as funções estão `Active`, com atualização `Successful`.
- Uma camada pública JWT para ambas as funções e uma camada privada anexada apenas a `identity-admin`. O par é o mesmo usado pela versão serverless e foi validado antes do upload. As funções receberam as variáveis de banco da Lambda existente, `DB_SSLMODE=require`, `DISABLE_SIGNAL_HANDLERS=true` e as localizações `file:///opt/keys/...` adequadas. Nenhum valor de senha ou token foi gravado nos logs de implantação.
- HTTP API `yourmenu-modular-http`, estágio `$default` com publicação automática, URL `https://yox4i6hq9k.execute-api.us-east-1.amazonaws.com`. Rotas de autenticação, usuários, restaurante e colaboradores vão a `identity-admin`; `$default` vai a `commerce`. O cadastro `POST /api/auth/registrar` e o caminho genérico `ANY /api/auth/{proxy+}` exigem AWS IAM, pois o endpoint permite solicitar a role `ADMINISTRADOR` sem autenticação na aplicação. As rotas específicas `POST /api/auth/login` e `GET /api/auth/test-auth` permanecem públicas. O primeiro bloqueio específico permitia passar pelo caminho com barra final; a proteção foi ampliada antes da validação final. Clientes anônimos recebem HTTP 403 no cadastro normal, com barra final e com caminho codificado.

O teste `python3 scripts/smoke_aws_modular.py` passou em **13 verificações** pela URL pública e por uma invocação Lambda autorizada com IAM: auth 200, catálogo público 200, zonas sem JWT 401, três variações de cadastro anônimo 403, CORS 200, cadastro temporário 201, login 200, validação do JWT nas duas funções 200, resumo de pedidos 200 e exclusão da conta temporária 204. Uma requisição HTTP assinada com AWS SigV4 ao cadastro, com corpo inválido, chegou à aplicação e retornou 400; a mesma rota sem assinatura retorna 403. Evidências: `aws-deployment-console.log`, `aws-deployment-state.json`, `aws-smoke-console.log`, `aws-smoke-results.json` e `aws-final-state.json` (configuração final sem credenciais). O RDS existente voltou ao estado `available` após o backup.

**URL da versão serverless existente:** após a implantação, a Function URL antiga foi temporariamente configurada com `AWS_IAM` e o smoke test modular passou novamente nas **13 verificações**. A pedido do usuário, ela foi restaurada para `AuthType=NONE`. Uma requisição externa anônima a `/api/auth/test-auth` retornou 200; `POST /api/auth/login` com corpo vazio retornou 400 da aplicação. A API modular tem endereço próprio: `https://yox4i6hq9k.execute-api.us-east-1.amazonaws.com`; seu teste de autenticação retornou 200 e login com corpo vazio retornou 400. `aws-old-url-restriction.json` registra apenas o estado temporário anterior, não a configuração atual. As regras IAM nas rotas de cadastro da **API modular** permanecem.

**Limites da validação:** os 68 cenários nativos locais passaram, mas a execução na AWS cobriu somente 13 verificações representativas. Não houve teste de carga, medição de consumo/custo, nem auditoria completa do esquema do banco. Na API modular, o cadastro público comum está indisponível enquanto a rota IAM permanecer protegida; para habilitá-lo, será necessária uma alteração da regra de registro.

Fontes: [Quarkus Lambda HTTP](https://quarkus.io/guides/aws-lambda-http/), [AWS runtime personalizado](https://docs.aws.amazon.com/lambda/latest/dg/runtimes-custom.html), [AWS HTTP API routes](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-develop-routes.html), [AWS Lambda com RDS](https://docs.aws.amazon.com/lambda/latest/dg/services-rds.html).
