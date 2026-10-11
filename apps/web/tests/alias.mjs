// Lets Node resolve the app's "@/" imports (and add the .ts extension) so lib modules can be tested without a bundler.
import { register } from "node:module";
register("./alias-hooks.mjs", import.meta.url);
