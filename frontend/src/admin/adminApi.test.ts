import { afterEach, describe, expect, it, vi } from "vitest";
import {
  api,
  createUser,
  getErrorMessage,
  getReportsSummary,
  listUsers,
  replaceUserSchedules,
  updateDistributionMembership,
  updateUserRoles,
  validationMessage,
} from "./adminApi";

const originalFetch = globalThis.fetch;

const ok = (body: unknown): Response =>
  ({
    ok: true,
    status: 200,
    json: async () => body,
  } as Response);

const fail = (status: number, detail: unknown): Response =>
  ({
    ok: false,
    status,
    json: async () => ({ detail }),
  } as Response);

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("FE-04 adminApi client & error handling", () => {
  it("translates known backend error codes to human-readable Russian messages", () => {
    expect(getErrorMessage({ status: 409, detail: "username_already_exists" })).toBe(
      "Пользователь с таким логином уже существует."
    );
    expect(getErrorMessage({ status: 409, detail: "cannot_deactivate_last_admin" })).toBe(
      "Нельзя деактивировать последнего администратора."
    );
    expect(getErrorMessage({ status: 409, detail: "cannot_deactivate_self" })).toBe(
      "Нельзя деактивировать собственную учетную запись."
    );
    expect(getErrorMessage({ status: 422, detail: "comment_required" })).toBe(
      "Укажите обязательное основание (решение руководителя)."
    );
    expect(getErrorMessage({ status: 422, detail: "schedule_intervals_overlap" })).toBe(
      "Интервалы графика пересекаются."
    );
    expect(getErrorMessage({ status: 403, detail: "insufficient_role" })).toBe(
      "Недостаточно прав для выполнения операции."
    );
  });

  it("extracts validation message array from Pydantic 422", () => {
    const errorDetail = [
      { loc: ["body", "username"], msg: "Field required" },
      { loc: ["body", "password"], msg: "String should have at least 1 characters" },
    ];
    const msg = validationMessage(errorDetail);
    expect(msg).toContain("Field required");
    expect(msg).toContain("String should have at least 1 characters");
    expect(getErrorMessage({ status: 422, detail: errorDetail })).toContain("Field required");
  });

  it("sends credentials same-origin and json content-type header", async () => {
    let capturedUrl = "";
    let capturedInit: RequestInit | undefined;

    globalThis.fetch = vi.fn().mockImplementation(async (url, init) => {
      capturedUrl = String(url);
      capturedInit = init;
      return ok({ id: 1, username: "admin", full_name: "Admin", is_active: true, roles: [], timezone: "Europe/Moscow" });
    });

    await listUsers();

    expect(capturedUrl).toBe("/api/v1/admin/users");
    expect(capturedInit?.credentials).toBe("same-origin");
    expect((capturedInit?.headers as Record<string, string>)?.["Content-Type"]).toBe("application/json");
  });

  it("handles user creation with correct payload and method", async () => {
    let capturedBody = "";
    globalThis.fetch = vi.fn().mockImplementation(async (_url, init) => {
      capturedBody = String(init?.body);
      return ok({ id: 10, username: "engineer1", full_name: "Engineer 1", is_active: true, roles: [{ id: 2, name: "L2" }], timezone: "Asia/Yekaterinburg" });
    });

    const user = await createUser({
      username: "engineer1",
      password: "secretpassword",
      full_name: "Engineer 1",
      roles: [2],
      is_active: true,
    });

    expect(user.id).toBe(10);
    const parsed = JSON.parse(capturedBody);
    expect(parsed.username).toBe("engineer1");
    expect(parsed.password).toBe("secretpassword");
    expect(parsed.roles).toEqual([2]);
  });

  it("handles distribution membership update with mandatory comment", async () => {
    let capturedBody = "";
    let capturedUrl = "";
    globalThis.fetch = vi.fn().mockImplementation(async (url, init) => {
      capturedUrl = String(url);
      capturedBody = String(init?.body);
      return ok({ id: 5, user_id: 15, pool_code: 2, is_enabled: true, comment: "Approved by manager" });
    });

    await updateDistributionMembership(15, {
      pool_code: 2,
      enabled: true,
      comment: "Approved by manager",
    });

    expect(capturedUrl).toBe("/api/v1/admin/distribution/members/15");
    const parsed = JSON.parse(capturedBody);
    expect(parsed.pool_code).toBe(2);
    expect(parsed.enabled).toBe(true);
    expect(parsed.comment).toBe("Approved by manager");
  });

  it("throws ApiError with status and detail on HTTP failure", async () => {
    globalThis.fetch = vi.fn().mockImplementation(async () => {
      return fail(409, "username_already_exists");
    });

    await expect(
      createUser({
        username: "duplicate",
        password: "pwd",
        full_name: "Dup",
        roles: [1],
      })
    ).rejects.toMatchObject({
      status: 409,
      detail: "username_already_exists",
    });
  });
});
