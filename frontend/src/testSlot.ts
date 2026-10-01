import { nextTick } from "vue";
import SlotPicker from "./SlotPicker.vue";

type Scope = { findComponent: (component: unknown) => { vm: { $emit: (event: string, value: string) => void } } };

/** Sets the value of the SlotPicker found inside a wrapper, like setValue on a datetime-local input. */
export function slotInput(scope: Scope) {
  return {
    async setValue(value: string): Promise<void> {
      scope.findComponent(SlotPicker).vm.$emit("update:modelValue", value);
      await nextTick();
    },
  };
}
