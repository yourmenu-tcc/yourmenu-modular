package tech.miqueias.bento.io;

import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import tech.miqueias.bento.io.usuario.UsuarioRepository;
import tech.miqueias.bento.io.colaborador.UsuarioRestauranteRepository;
import tech.miqueias.bento.io.restaurante.RestauranteRepository;
import java.util.Set;
import java.util.stream.Collectors;
import static org.junit.jupiter.api.Assertions.*;

@QuarkusTest
class SharedPersistenceTest {
    @Inject EntityManager em;
    @Inject UsuarioRepository usuarios;
    @Inject RestauranteRepository restaurantes;
    @Inject UsuarioRestauranteRepository vinculos;

    @Test
    void discoversSharedEntitiesAndInjectsUsableRepositories() {
        var entities = em.getMetamodel().getEntities().stream()
                .map(entity -> entity.getJavaType().getSimpleName()).collect(Collectors.toSet());
        assertEquals(Set.of("Usuario", "Restaurante", "UsuarioRestaurante", "MyEntity"), entities);
        assertDoesNotThrow(() -> usuarios.count());
        assertDoesNotThrow(() -> restaurantes.count());
        assertDoesNotThrow(() -> vinculos.count());
    }
}
