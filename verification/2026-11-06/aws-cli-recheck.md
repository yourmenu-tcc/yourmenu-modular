# Nova verificação da AWS CLI — 06/11/2026

Consultas somente de leitura, região `us-east-1`. `aws` via Snap ainda falha dentro do sandbox com mensagem sobre `snapd.apparmor`; executado fora do sandbox, `aws --version` retornou CLI 2.35.21 e `aws sts get-caller-identity` autenticou como `desenvolvedor-tcc`. Nenhum recurso AWS foi criado, alterado ou excluído.

Recursos observados:

- RDS `yourmenu-database`: PostgreSQL 16.15, estado `available`, VPC `vpc-07b04431343f7d0cd`, privado. Seu grupo de segurança permite a porta 5432 a partir de `lambda-app-sg`.
- Lambda atual `yourmenu-quarkus-serverless`: `provided.al2023`, x86-64, 1024 MB, 10 s, mesma VPC e grupo `lambda-app-sg`. A função é exposta por uma Function URL. Sua role tem políticas de logs e acesso à VPC; as variáveis de ambiente possuem as cinco chaves `DB_*` necessárias, sem `DB_SSLMODE` explícito.
- Nenhuma HTTP API ou REST API no API Gateway; nenhuma função modular; nenhum segredo listado no Secrets Manager.
- O usuário IAM possui política `AdministratorAccess`. A autenticação e as consultas de leitura foram testadas; nenhuma operação de implantação foi executada.

Conclusão: a CLI agora pode ser usada para preparar a implantação. Os dois ZIPs nativos estão verificados em `artifacts/` e no relatório `README.md`. Para publicar a arquitetura planejada, ainda é necessário configurar duas funções Lambda, distribuição das chaves JWT, a mesma rede/banco e uma HTTP API única que encaminhe cada rota à função correta. O esquema existente no RDS e a execução na nuvem não foram verificados nesta consulta.

Atualização posterior: os recursos foram criados e testados. O resultado final está em [`README.md`](README.md), seção “Implantação realizada”.
