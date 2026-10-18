import { stopServices } from "./processes";

export default function globalTeardown(): void {
  stopServices();
}
