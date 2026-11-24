import { Outlet } from "react-router-dom";
import Masthead from "./Masthead.jsx";

export default function Layout() {
  return (
    <div className="sheet">
      <Masthead />
      <Outlet />
      <div className="bottom-rules" aria-hidden="true">
        <span />
        <span />
      </div>
    </div>
  );
}
