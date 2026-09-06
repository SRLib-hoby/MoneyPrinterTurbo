import { Container } from "@cloudflare/containers";
import { handleRequest } from "./handler";

interface Env {
  QINGZHI_CONTAINER: DurableObjectNamespace<QingzhiContainer>;
  APP_ACCESS_PASSWORD: string;
  DEEPSEEK_API_KEY?: string;
  MINIMAX_API_KEY?: string;
  R2_ENDPOINT_URL: string;
  R2_BUCKET_NAME: string;
  R2_ACCESS_KEY_ID: string;
  R2_SECRET_ACCESS_KEY: string;
}

export class QingzhiContainer extends Container<Env> {
  defaultPort = 8501;
  sleepAfter = "60m";
  enableInternet = true;
  pingEndpoint = "localhost/_stcore/health";
  envVars = {
    DEEPSEEK_API_KEY: this.env.DEEPSEEK_API_KEY ?? "",
    MINIMAX_API_KEY: this.env.MINIMAX_API_KEY ?? "",
    QINGZHI_PERSISTENCE: "r2",
    R2_ENDPOINT_URL: this.env.R2_ENDPOINT_URL ?? "",
    R2_BUCKET_NAME: this.env.R2_BUCKET_NAME ?? "",
    R2_ACCESS_KEY_ID: this.env.R2_ACCESS_KEY_ID ?? "",
    R2_SECRET_ACCESS_KEY: this.env.R2_SECRET_ACCESS_KEY ?? "",
  };
}

export default { fetch: handleRequest } satisfies ExportedHandler<Env>;
