/**
 * Helper functions for Omnidesk parent page integration.
 * Extracts untrusted ticket number and case_id from URL and DOM,
 * and manages secure postMessage communication with child iframe.
 */

export interface OmnideskExtractedContext {
  ticketNumber: string | null;
  caseId: string | null;
}

/**
 * Extracts ticket number from Omnidesk URL pattern `/user/cases/record/{case_number}/`.
 */
export function extractTicketNumberFromUrl(urlOrPath: string): string | null {
  if (!urlOrPath) return null;
  try {
    // Match /user/cases/record/{case_number}/ or /cases/record/{case_number}/
    const match = urlOrPath.match(/(?:^|\/)(?:user\/)?cases\/record\/([^/?#]+)/i);
    if (!match || !match[1]) return null;
    const candidate = decodeURIComponent(match[1]).trim();
    // Validate format: safe characters only, no control chars or HTML
    if (/^[a-zA-Z0-9_\-\.]+$/.test(candidate)) {
      return candidate;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Extracts case_id from DOM context as untrusted input.
 * Explicitly searches known DOM markers and validates numeric string format.
 */
export function extractCaseIdFromDom(doc: Document = document): string | null {
  if (!doc) return null;

  // 1. Check data-case-id attribute on any container
  const elDataCaseId = doc.querySelector("[data-case-id]");
  if (elDataCaseId) {
    const val = elDataCaseId.getAttribute("data-case-id")?.trim();
    if (val && /^\d+$/.test(val)) return val;
  }

  // 2. Check input[name="case_id"]
  const inputCaseId = doc.querySelector<HTMLInputElement>('input[name="case_id"]');
  if (inputCaseId && inputCaseId.value) {
    const val = inputCaseId.value.trim();
    if (/^\d+$/.test(val)) return val;
  }

  // 3. Check data-case attribute
  const elDataCase = doc.querySelector("[data-case]");
  if (elDataCase) {
    const val = elDataCase.getAttribute("data-case")?.trim();
    if (val && /^\d+$/.test(val)) return val;
  }

  // 4. Check element with id="case_id"
  const elIdCase = doc.getElementById("case_id");
  if (elIdCase) {
    const val = (
      (elIdCase as HTMLInputElement).value ||
      elIdCase.getAttribute("data-id") ||
      elIdCase.textContent ||
      ""
    ).trim();
    if (/^\d+$/.test(val)) return val;
  }

  // 5. Check data-record-id attribute
  const elRecordId = doc.querySelector("[data-record-id]");
  if (elRecordId) {
    const val = elRecordId.getAttribute("data-record-id")?.trim();
    if (val && /^\d+$/.test(val)) return val;
  }

  return null;
}

/**
 * Verifies whether an origin is allowed based on explicit allowed list or wildcard matcher.
 */
export function isOriginAllowed(origin: string, allowedOrigins: string[]): boolean {
  if (!origin || !allowedOrigins || allowedOrigins.length === 0) return false;
  if (allowedOrigins.includes("*")) return false;
  return allowedOrigins.some((allowed) => {
    try {
      const allowedUrl = new URL(allowed);
      const originUrl = new URL(origin);
      return allowedUrl.origin === originUrl.origin;
    } catch {
      return allowed === origin;
    }
  });
}

export interface ParentIntegrationOptions {
  iframeElement: HTMLIFrameElement;
  targetOrigin: string;
  allowedOrigins?: string[];
  ticketNumber?: string | null;
  caseId?: string | null;
  onReady?: () => void;
}

export function initParentIntegration(options: ParentIntegrationOptions): () => void {
  const { iframeElement, targetOrigin, allowedOrigins = [targetOrigin], ticketNumber, caseId } = options;

  function sendInit() {
    if (!iframeElement.contentWindow) return;
    if (!ticketNumber || !caseId) return;

    iframeElement.contentWindow.postMessage(
      {
        type: "RDM_FRAME_INIT",
        payload: {
          omnidesk_ticket_number: ticketNumber,
          case_id: caseId,
        },
      },
      targetOrigin
    );
  }

  function handleMessage(event: MessageEvent) {
    if (event.source !== iframeElement.contentWindow || !isOriginAllowed(event.origin, allowedOrigins)) {
      return;
    }

    if (event.data && event.data.type === "RDM_FRAME_READY") {
      if (options.onReady) options.onReady();
      sendInit();
    }
  }

  window.addEventListener("message", handleMessage);

  // If iframe already loaded, attempt sendInit
  iframeElement.addEventListener("load", () => {
    sendInit();
  });

  return () => {
    window.removeEventListener("message", handleMessage);
  };
}
