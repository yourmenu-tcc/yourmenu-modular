# YourMenu modular — fase 1

Terceira implementação experimental, com Java 25, Quarkus 3.38.1, PostgreSQL e duas aplicações Lambda HTTP. A base é `../yourmenu-serverless`; os dois projetos originais não são dependências Maven deste projeto.

| Módulo | Conteúdo | Java de produção |
|---|---|---:|
| `common` | Usuário, restaurante, vínculo e seus repositórios; roles/status; filtros e contexto tenant; exceções; definição OpenAPI | 16 |
| `identity-admin` | Controladores, serviços e DTOs de auth, usuários, restaurantes e colaboradores; exemplos `/hello` e `MyEntity` preservados | 21 |
| `commerce` | Controladores, serviços, DTOs, entidades e repositórios de categorias, pratos, zonas de entrega e pedidos | 40 |

Os 77 arquivos Java foram movidos por cópia para proprietários únicos, preservando conteúdo, pacote e nome. O inventário exato está em `class-distribution.json`. `common` não depende das aplicações, e nenhuma aplicação depende da outra. A descoberta CDI/JPA do JAR comum é habilitada por `common/src/main/resources/META-INF/beans.xml`. As associações são direcionadas: entidades de commerce apontam para restaurante, mas restaurante não aponta para elas. Portanto, não é necessário colocar o domínio de pedidos no módulo comum.

## Build JVM independente

Configure `JAVA_HOME` para seu JDK 25. O wrapper preservado usa Maven 3.9.16.

```sh
./mvnw -pl identity-admin -am package -DskipTests
./mvnw -pl commerce -am package -DskipTests
python3 scripts/verify_layout.py
```

`-am` compila o pai e `common` junto da aplicação escolhida, sem precisar instalar a outra aplicação. Cada aplicação gera seu próprio `target/function.zip` e `target/*-runner.jar`, apropriados ao adaptador Lambda HTTP. Os templates SAM gerados pelo Quarkus são exemplos do framework, não uma definição aprovada de implantação/routing do experimento.

## Configuração externa

| Variável | Uso |
|---|---|
| `DB_USERNAME`, `DB_PASSWORD` | Obrigatórias, sem valores de credencial padrão |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_SSLMODE` | Mesmo PostgreSQL/esquema; padrões estruturais preservados: localhost, 5432, yourmenu_db e require |
| `JWT_PUBLIC_KEY_LOCATION` | URI/localização externa da chave pública aceita por ambas as aplicações |
| `JWT_PRIVATE_KEY_LOCATION` | Localização externa da chave de assinatura, somente em identity-admin |

O issuer permanece `comandas-api`; claims, duração, autorização e regras de negócio permanecem no código original. Não há chaves PEM ou credenciais de banco no projeto. Forneça as mesmas chaves/configurações de confiança nos ambientes; gerar chaves diferentes para cada função quebraria a interoperabilidade.

A atualização automática de esquema está desabilitada (`schema-management.strategy=none`) em produção, desenvolvimento e testes. `sql-load-script=no-file` evita carga implícita; `quarkus.native.resources.includes=**/*.sql` e o `import.sql` original (somente comentários) foram preservados. O esquema completo precisa ser preparado previamente por um procedimento único e controlado. Não inicializar produção com o schema parcial de identity-admin.

## Validação funcional local

Para usar os dois módulos manualmente pelo Insomnia, com banco e chaves de teste descartáveis, execute na raiz:

```sh
python3 scripts/serve_local.py
```

O comando compila os módulos, inicia PostgreSQL local em Docker e disponibiliza uma API unificada em `http://127.0.0.1:8080`. `identity-admin` atende autenticação, usuários, restaurante e colaboradores; `commerce` atende catálogo, zonas e pedidos. O banco começa vazio, portanto contas antigas não estarão disponíveis. Pressione Ctrl+C para encerrar e remover o banco e as chaves; use `--port 8082` se a porta 8080 estiver ocupada. Esse iniciador executa os artefatos Lambda da versão 3.38.1 e evita a falha conhecida do `quarkus:dev` nessa versão.

Para a validação automatizada completa:

Requer Docker, OpenSSL, Python 3, Java 25 e os dois artefatos JVM construídos. Execute:

```sh
python3 scripts/validate_local.py
```

Caso necessário, `DOCKER` pode apontar para o executável Docker. O script sempre cria seu próprio PostgreSQL `16-alpine`, publica uma porta aleatória apenas em localhost e remove apenas esse container ao final. Nunca aceita um banco externo. Chaves RSA e senha são geradas em diretório temporário externo ao projeto e eliminadas ao terminar. Inicializa o esquema uma vez com commerce, em banco vazio descartável, e reinicia ambas as aplicações com alterações de esquema desabilitadas.

As duas aplicações empacotadas recebem eventos HTTP v2 por um simulador mínimo local da Lambda Runtime API, usando o adaptador real do Quarkus. O script utiliza a propriedade de teste interna `quarkus-internal.aws-lambda.test-api` da versão 3.38.1; não é um componente de produção. Em seguida, executa `verify -DskipITs=false` separadamente para cada aplicação, com o wrapper e o mesmo PostgreSQL descartável. Não executa carga, otimizações nem chamadas AWS.

Resultados e logs ficam em `target/validation`; relatórios JUnit ficam em `*/target/surefire-reports` e `identity-admin/target/failsafe-reports`. O resultado diferencia testes de equivalência aprovados de riscos herdados observados. Um retorno bem-sucedido do script não significa certificação completa de segurança, concorrência ou implantação.

## Build nativo

O perfil `native` está disponível; compilação JVM não aprova a compilação nativa. Com GraalVM/Mandrel JDK 25 e recursos suficientes, por exemplo:

```sh
./mvnw -pl identity-admin -am package -Dnative -DskipTests -Dquarkus.native.container-build=true
./mvnw -pl commerce -am package -Dnative -DskipTests -Dquarkus.native.container-build=true
```

Esses comandos substituem os artefatos nos respectivos `target`; execute novamente o build JVM antes do script local. Após uma compilação nativa bem-sucedida, ainda é necessário executar testes funcionais com os binários nativos. O relatório de entrega em `../docs/manutenibilidade/implementacao_modular_fase1.md` registra o que realmente foi validado.

## Risco herdado confirmado

A validação reproduziu uma falha do código de referência: um gerente de A pode ler zonas de B com caminho de B e `X-Restaurante-Id: A`. O filtro valida o cabeçalho, enquanto a consulta usa o caminho sem repetir a autorização. A fonte foi preservada nesta decomposição; o relatório registra esse risco e a decisão necessária sobre correção consistente nas variantes. O isolamento completo e a implantação não estão aprovados.

## Implantação posterior

A fase 1 não cria infraestrutura. Uma futura solicitação deve definir o roteamento de `/api/auth`, `/api/usuarios`, `/api/restaurante` e colaboradores para identity-admin, e categorias/pratos/zonas/pedidos para commerce, mantendo os métodos e subcaminhos. Planejar somente `us-east-1`, mesmo banco, segredos externos, permissões, rede, esquema preparado, conexões e limites de concorrência. Não pressupor que dois templates SAM gerados isoladamente forneçam um endpoint público único.
