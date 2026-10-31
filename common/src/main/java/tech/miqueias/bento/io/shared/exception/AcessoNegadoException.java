package tech.miqueias.bento.io.shared.exception;

public class AcessoNegadoException extends DomainException {

    public AcessoNegadoException() {
        super("Acesso negado.");
    }

    public AcessoNegadoException(String message) {
        super(message);
    }
    
}
