import { RouterProvider, createBrowserRouter } from "react-router";
import { appRoutes } from "./routes";
import "./styles/layout.css";

const router = createBrowserRouter(appRoutes);

export function App() {
  return <RouterProvider router={router} />;
}
