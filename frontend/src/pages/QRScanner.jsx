import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Html5QrcodeScanner } from "html5-qrcode";
import PageShell from "../components/PageShell";
import { routeForScan } from "../lib/scanRoute";
import { ScanIcon } from "../components/Icons";

function QRScanner() {
  const navigate = useNavigate();

  useEffect(() => {
    const scanner = new Html5QrcodeScanner(
      "reader",
      { qrbox: { width: 250, height: 250 }, fps: 5 },
      false
    );

    scanner.render(
      (decodedText) => {
        scanner.clear().catch(() => {});
        navigate(routeForScan(decodedText));
      },
      () => {}
    );

    return () => {
      scanner.clear().catch(() => {});
    };
  }, [navigate]);

  return (
    <PageShell
      width="md"
      icon={ScanIcon}
      title="Scan QR"
      subtitle="Scan the attendance or enrollment QR shown by your faculty"
      back={{ to: "/student-portal", label: "Student Portal" }}
    >
      <div className="ft-scanner">
        <div id="reader" />
      </div>
    </PageShell>
  );
}

export default QRScanner;
