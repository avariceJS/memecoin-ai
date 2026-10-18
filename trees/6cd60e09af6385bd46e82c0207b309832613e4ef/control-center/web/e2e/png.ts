export function pngSize(data: Buffer): { width: number; height: number } {
  const signature = data.subarray(0, 8).toString("hex");
  if (data.length < 24 || signature !== "89504e470d0a1a0a") {
    throw new Error("Снимок не PNG");
  }
  const type = data.subarray(12, 16).toString("ascii");
  if (type !== "IHDR") {
    throw new Error("PNG без IHDR");
  }
  return { width: data.readUInt32BE(16), height: data.readUInt32BE(20) };
}
