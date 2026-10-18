import { startServices } from "./processes";

export default async function globalSetup(): Promise<void> {
  await startServices();
}
