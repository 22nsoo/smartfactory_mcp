# Risk Score 해석 원칙

## 점수의 의미

Risk Score는 Isolation Forest severity를 Validation 분포의 경험적 백분위수로 변환한 상대 점수다. 설비 고장 확률, 잔여 수명 또는 안전 등급을 직접 의미하지 않는다.

## 상태 등급

0 이상 60 미만은 NORMAL, 60 이상 80 미만은 ATTENTION, 80 이상 95 미만은 DEGRADING, 95 이상은 WARNING으로 표시한다. 이 임계값은 프로젝트의 초기 운영 기준이며 고장 라벨로 검증된 절대 기준이 아니다.

## 판단 방법

Risk Score와 함께 3-Sigma 탐지 Feature, sample_count, 직전 수집 공백, 최근 추세와 생산 조건을 확인한다. WARNING 한 건보다 일정 기간 반복되는 이상, 여러 Feature의 동시 변화와 관련 센서의 동시 변화를 더 중요하게 검토한다.

## 금지되는 해석

Risk Score가 높다는 이유만으로 설비 고장이라고 단정하거나 즉시 부품을 교체하지 않는다. 반대로 NORMAL도 고장이 없음을 보증하지 않는다. 최종 판단에는 현장 점검, 제조사 기준, 작업 이력과 자격을 갖춘 담당자의 검토가 필요하다.
