import { describe, expect, it, vi } from "vitest";
import {
  extractCaseIdFromDom,
  extractTicketNumberFromUrl,
  initParentIntegration,
  isOriginAllowed,
} from "./omnideskParent";

describe("omnideskParent helpers", () => {
  describe("extractTicketNumberFromUrl", () => {
    it("extracts ticket number from standard Omnidesk URL", () => {
      const url = "https://subdomain.omnidesk.ru/user/cases/record/123-456789/";
      expect(extractTicketNumberFromUrl(url)).toBe("123-456789");
    });

    it("extracts ticket number from localized URL path", () => {
      const url = "https://iridi.omnidesk.ru/l_rus/user/cases/record/987-654321/";
      expect(extractTicketNumberFromUrl(url)).toBe("987-654321");
    });

    it("extracts ticket number from short cases/record path", () => {
      const url = "/cases/record/ABC-12345/";
      expect(extractTicketNumberFromUrl(url)).toBe("ABC-12345");
    });

    it("rejects invalid URL patterns and unsafe characters", () => {
      expect(extractTicketNumberFromUrl("https://example.com/cases/list/")).toBeNull();
      expect(extractTicketNumberFromUrl("/user/cases/record/<script>/")).toBeNull();
      expect(extractTicketNumberFromUrl("")).toBeNull();
    });
  });

  describe("extractCaseIdFromDom", () => {
    it("extracts case_id from data-case-id attribute", () => {
      const doc = document.implementation.createHTMLDocument();
      doc.body.innerHTML = '<div data-case-id="54321"><span>Case view</span></div>';
      expect(extractCaseIdFromDom(doc)).toBe("54321");
    });

    it("extracts case_id from input[name='case_id']", () => {
      const doc = document.implementation.createHTMLDocument();
      doc.body.innerHTML = '<input type="hidden" name="case_id" value="98765" />';
      expect(extractCaseIdFromDom(doc)).toBe("98765");
    });

    it("extracts case_id from data-case attribute", () => {
      const doc = document.implementation.createHTMLDocument();
      doc.body.innerHTML = '<section data-case="112233"></section>';
      expect(extractCaseIdFromDom(doc)).toBe("112233");
    });

    it("extracts case_id from #case_id element", () => {
      const doc = document.implementation.createHTMLDocument();
      doc.body.innerHTML = '<span id="case_id" data-id="445566">Case</span>';
      expect(extractCaseIdFromDom(doc)).toBe("445566");
    });

    it("rejects non-numeric values as untrusted input", () => {
      const doc = document.implementation.createHTMLDocument();
      doc.body.innerHTML = '<div data-case-id="case-123-abc"></div>';
      expect(extractCaseIdFromDom(doc)).toBeNull();
    });

    it("returns null when no matching DOM markers exist", () => {
      const doc = document.implementation.createHTMLDocument();
      doc.body.innerHTML = '<div>No case info here</div>';
      expect(extractCaseIdFromDom(doc)).toBeNull();
    });
  });

  describe("isOriginAllowed", () => {
    it("matches exact allowed origin", () => {
      expect(isOriginAllowed("https://booking.example.com", ["https://booking.example.com"])).toBe(true);
    });

    it("rejects mismatched origin", () => {
      expect(isOriginAllowed("https://evil.example.com", ["https://booking.example.com"])).toBe(false);
    });

    it("rejects wildcard origins", () => {
      expect(isOriginAllowed("https://any.example.com", ["*"])).toBe(false);
    });
  });

  describe("initParentIntegration postMessage handshake", () => {
    it("sends RDM_FRAME_INIT when receiving RDM_FRAME_READY from allowed origin", () => {
      const iframe = document.createElement("iframe");
      const postMessageSpy = vi.fn();
      Object.defineProperty(iframe, "contentWindow", {
        value: { postMessage: postMessageSpy },
        writable: true,
      });

      const cleanup = initParentIntegration({
        iframeElement: iframe,
        targetOrigin: "https://booking.example.test",
        ticketNumber: "123-456789",
        caseId: "2000",
      });

      // Simulate message from iframe
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: "https://booking.example.test",
          source: iframe.contentWindow,
          data: { type: "RDM_FRAME_READY" },
        })
      );

      expect(postMessageSpy).toHaveBeenCalledWith(
        {
          type: "RDM_FRAME_INIT",
          payload: {
            omnidesk_ticket_number: "123-456789",
            case_id: "2000",
          },
        },
        "https://booking.example.test"
      );

      cleanup();
    });

    it("ignores messages from disallowed origins", () => {
      const iframe = document.createElement("iframe");
      const postMessageSpy = vi.fn();
      Object.defineProperty(iframe, "contentWindow", {
        value: { postMessage: postMessageSpy },
        writable: true,
      });

      const cleanup = initParentIntegration({
        iframeElement: iframe,
        targetOrigin: "https://booking.example.test",
        ticketNumber: "123-456789",
        caseId: "2000",
      });

      // Simulate message from attacker origin
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: "https://attacker.invalid",
          data: { type: "RDM_FRAME_READY" },
        })
      );

      expect(postMessageSpy).not.toHaveBeenCalled();

      cleanup();
    });

    it("ignores a matching origin when the message is not from the iframe", () => {
      const iframe = document.createElement("iframe");
      const postMessageSpy = vi.fn();
      Object.defineProperty(iframe, "contentWindow", { value: { postMessage: postMessageSpy } });
      const cleanup = initParentIntegration({
        iframeElement: iframe,
        targetOrigin: "https://booking.example.test",
        ticketNumber: "123-456789",
        caseId: "2000",
      });
      window.dispatchEvent(new MessageEvent("message", {
        origin: "https://booking.example.test",
        source: window,
        data: { type: "RDM_FRAME_READY" },
      }));
      expect(postMessageSpy).not.toHaveBeenCalled();
      cleanup();
    });
  });
});
