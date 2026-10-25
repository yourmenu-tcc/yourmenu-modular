package tech.miqueias.bento.io.shared.tenant_context;

import org.junit.jupiter.api.Test;
import tech.miqueias.bento.io.usuario.Role;
import tech.miqueias.bento.io.usuario.Usuario;
import java.util.Set;
import static org.junit.jupiter.api.Assertions.*;

class TenantCleanupTest {
    @Test
    void responseFilterClearsAllIdentityStateBeforeThreadReuse() {
        TenantContext context = new TenantContext();
        TenantResponseFilter responseFilter = new TenantResponseFilter();
        responseFilter.tenantContext = context;
        Usuario first = new Usuario("First", "first@example.test", "unused", Set.of(Role.GERENTE));
        context.bind(first, 1L, Role.GERENTE);
        responseFilter.filter(null, null);
        assertTrue(context.getUsuario().isEmpty());
        assertTrue(context.getRestauranteId().isEmpty());
        assertTrue(context.getPapel().isEmpty());
        Usuario second = new Usuario("Second", "second@example.test", "unused", Set.of(Role.FUNCIONARIO));
        context.bind(second, 2L, Role.FUNCIONARIO);
        assertSame(second, context.getUsuario().orElseThrow());
        assertEquals(2L, context.getRestauranteId().orElseThrow());
        assertEquals(Role.FUNCIONARIO, context.getPapel().orElseThrow());
        responseFilter.filter(null, null);
    }
}
