import com.sun.tools.attach.VirtualMachine;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.Paths;

/** Explicit PID only; caller verifies same-user target identity before invoking. */
public final class AttachRender {
    public static void main(String[] args) throws Exception {
        if (args.length != 3 || !args[0].matches("[1-9][0-9]{0,9}"))
            throw new IllegalArgumentException("Usage: AttachRender PID ABSOLUTE_AGENT_JAR ABSOLUTE_OUTPUT");
        Path agent = Paths.get(args[1]);
        Path output = Paths.get(args[2]);
        if (!agent.isAbsolute() || !output.isAbsolute() || !agent.equals(agent.normalize()) ||
            !output.equals(output.normalize()) || !Files.isRegularFile(agent, LinkOption.NOFOLLOW_LINKS) ||
            Files.exists(output, LinkOption.NOFOLLOW_LINKS)) throw new IllegalArgumentException("UnsafePath");
        VirtualMachine machine = VirtualMachine.attach(args[0]);
        try { machine.loadAgent(agent.toString(), output.toString()); }
        finally { machine.detach(); }
        System.out.println("Render diagnostic task scheduled; inspect the private output file.");
    }
}
