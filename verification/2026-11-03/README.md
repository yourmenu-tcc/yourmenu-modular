# Verificação de build nativo e prontidão AWS — 03/11/2026

Revisão `07a6f95`, Quarkus 3.38.1, Maven Wrapper 3.9.16, Java local 25.0.4.1, Mandrel no contêiner 25.0.5.0. A máquina tinha 6,9 GiB de RAM total e swap já em uso. Nenhum recurso AWS foi criado ou alterado. O código da aplicação não foi modificado nesta verificação.

## Resultado comprovado

| Etapa | Resultado | Evidência |
|---|---|---|
| `identity-admin` nativo completo | Falhou durante a geração da imagem, com `OutOfMemoryError: Java heap space`, após 6min33s. Heap limitado a 2.400 MB e duas threads. Não houve erro de compilação Java demonstrado nessa tentativa. | `native-identity-admin.log` |
| `commerce` com `native-sources=true` | Falhou em 9,581s porque a extensão Lambda HTTP do Quarkus rejeita o tipo `native-sources`. Esse comando diagnóstico não substitui um build nativo completo. | `native-commerce-sources-only.log` |
| `commerce` nativo completo | Não executado nesta máquina após a falha de memória do primeiro módulo. | — |
| Build JVM dos dois módulos | Passou após as tentativas nativas; gerou os dois `target/function.zip` JVM. | `jvm-package.log` |
| Compatibilidade dos exemplos SAM JVM | Os templates gerados declaram `Runtime: java21`, mas as classes compiladas usam bytecode Java 25 (major version 69). Esses templates não podem publicar os ZIPs JVM atuais sem ajuste da estratégia de runtime/build. | `pom.xml` (`maven.compiler.release=25`), `target/sam.jvm.yaml`, `javap -verbose` |
| Validação funcional local dos artefatos JVM | 70 verificações aprovadas, sem observações, incluindo autenticação entre módulos, isolamento por restaurante e resumo de pedidos. Banco PostgreSQL 16 descartável. | `jvm-validation.log`, `results.json` |
| Maven `verify` com testes | Passou nos dois reatores. `identity-admin`: 5 testes de `common`, 2 unitários e 1 de integração; `commerce`: 5 testes de `common` e 3 unitários. São 16 execuções de testes nos dois reatores, com os 5 testes de `common` repetidos. Zero falhas, erros ou testes ignorados. | `maven-identity-admin-verify.log`, `maven-commerce-verify.log` |

Comando nativo executado para `identity-admin`:

```sh
./mvnw -B -pl identity-admin -am package -Dnative -DskipTests \
  -Dquarkus.native.container-build=true \
  -Dquarkus.native.native-image-xmx=2400m \
  -Dquarkus.native.additional-build-args=--parallelism=2 \
  -Dquarkus.native.march=x86-64-v2 \
  -Dquarkus.analytics.disabled=true
```

Comando diagnóstico executado para `commerce`:

```sh
./mvnw -B -pl commerce -am package -Dnative -DskipTests \
  -Dquarkus.native.sources-only=true \
  -Dquarkus.native.container-build=true \
  -Dquarkus.native.builder-image.pull=never \
  -Dquarkus.native.march=x86-64-v2 \
  -Dquarkus.analytics.disabled=true
```

A extensão Lambda rejeitou esse modo antes de iniciar a compilação nativa. Depois foram executados `./mvnw -B -pl identity-admin,commerce -am clean package -DskipTests -Dquarkus.analytics.disabled=true` e `python3 scripts/validate_local.py`.

Os ZIPs atuais são **JVM**: ambos não contêm `bootstrap` na raiz. Portanto, não são artefatos nativos para `provided.al2023`. O arquivo `SHA256SUMS` identifica os logs e o resultado preservados.

## Condições pendentes para a implantação

1. Gerar os **dois** `function.zip` nativos em máquina de build com mais memória disponível, mantendo arquitetura compatível com Lambda AL2023 (nesta tentativa, `x86-64-v2`). Conferir `bootstrap` executável na raiz dos ZIPs e executar os binários nativos com banco e chaves de teste. A falta de memória nesta máquina não comprova defeito da aplicação nativa nem sucesso dela.
2. Definir infraestrutura de implantação para **uma** HTTP API com rotas de `identity-admin` (`/api/auth`, `/api/usuarios`, `/api/restaurante`, `/api/restaurantes/{restauranteId}/colaboradores`) e de `commerce` (categorias, pratos, zonas e pedidos), incluindo subcaminhos e métodos. Os `target/sam.native.yaml` gerados são independentes, cada um com sua própria API implícita, e não fazem esse roteamento.
3. Configurar as duas funções na mesma região e com acesso ao mesmo PostgreSQL/esquema. Preparar o esquema completo antes da primeira invocação; `%prod.quarkus.hibernate-orm.schema-management.strategy=none` e não há migrações versionadas neste projeto. Definir rede/VPC, permissões, conexões e limites de concorrência conforme o banco escolhido.
4. Provisionar `DB_USERNAME`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_SSLMODE` e a localização da chave pública JWT nas duas funções; disponibilizar também a chave privada de assinatura somente ao `identity-admin`. As localizações de chave precisam resolver para recursos acessíveis em execução, e as duas funções precisam compartilhar a mesma chave pública. Incluir `DISABLE_SIGNAL_HANDLERS=true` no runtime nativo.
5. Configurar e testar limites de memória/tempo da Lambda e a integração real com API Gateway, banco e JWT. Os exemplos SAM usam 128 MB e 15 s sem medição do binário nativo. O teste local não valida a integração AWS.

O README do projeto registra a configuração externa e o histórico da fase 1. A falha de isolamento de zonas descrita naquela fase foi corrigida posteriormente: esta validação local passou nos cenários de A/B. A configuração CORS atual lista apenas `Content-Type,Authorization` como cabeçalhos aceitos; se um cliente web usar `X-Restaurante-Id`, será necessário verificar e ajustar o preflight.

**Conclusão:** a versão JVM está validada localmente; o build nativo e a implantação AWS ainda não estão aprovados.

Referências técnicas: [Quarkus Lambda HTTP](https://quarkus.io/guides/aws-lambda-http/), [Quarkus Native Image](https://quarkus.io/guides/building-native-image/), [runtime personalizado da AWS Lambda](https://docs.aws.amazon.com/lambda/latest/dg/runtimes-custom.html), [rotas da HTTP API](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-develop-routes.html).
