import next from "eslint-config-next";
import prettier from "eslint-config-prettier";

const config = [...next, prettier, { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] }];
export default config;
