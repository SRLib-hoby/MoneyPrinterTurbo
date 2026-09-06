import { Container } from "@cloudflare/containers";
import { handleRequest } from "./handler";

interface Env {
  QINGZHI_CONTAINER: DurableObjectNamespace<QingzhiContainer>;
  APP_ACCESS_PASSWORD: string;
  DEEPSEEK_API_KEY?: string;
  MINIMAX_API_KEY?: string;
}

export class QingzhiContainer extends Container<Env> {
  defaultPort = 8501;
  sleepAfter = "60m";
  enableInternet = true;
  pingEndpoint = "localhost/_stcore/health";
  envVars = {
    DEEPSEEK_API_KEY: this.env.DEEPSEEK_API_KEY ?? "",
    MINIMAX_API_KEY: this.env.MINIMAX_API_KEY ?? "",
    QINGZHI_EPHEMERAL_STORAGE: "1",
  };
}

export default { fetch: handleRequest } satisfies ExportedHandler<Env>;
